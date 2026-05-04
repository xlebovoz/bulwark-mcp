"""stdio proxy between an MCP client (e.g. Claude Desktop) and an MCP server.

See ADR-0001. The shape is intentionally small:

- We launch the real server as a subprocess with no shell (argv form).
- Two pumps copy newline-delimited frames between the client and the server.
- Every frame is parsed best-effort and pushed onto an :class:`EventBuffer`
  for asynchronous, batched persistence.
- We never write our own diagnostics to stdout — that channel belongs to
  the JSON-RPC frames flowing to the client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, cast

from .config import Settings
from .models import EventRecord, parse_frame, split_batch
from .storage import EventBuffer, Storage

Direction = Literal["client_to_server", "server_to_client"]

logger = logging.getLogger(__name__)

DEFAULT_LINE_LIMIT_BYTES = 8 * 1024 * 1024  # 8 MiB — generous for tool results


@dataclass(frozen=True)
class ProxyResult:
    exit_code: int
    events_dropped: int


async def run_proxy(
    server_command: str,
    *,
    settings: Settings,
    line_limit: int = DEFAULT_LINE_LIMIT_BYTES,
) -> ProxyResult:
    """Run the proxy until either side closes. Returns the child's exit code."""
    argv = shlex.split(server_command)
    if not argv:
        raise ValueError("--server must contain an executable")

    storage = Storage(settings.db_path)
    await storage.open()
    try:
        session_id = await storage.start_session(
            server_command=server_command,
            client_pid=os.getppid(),
        )

        child = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr.fileno(),
        )
        if child.stdin is None or child.stdout is None:
            raise RuntimeError("subprocess pipes were not created — check argv")

        await _patch_session_with_server_pid(storage, session_id, child.pid)

        client_reader = await _connect_stdin(line_limit)
        client_writer = await _connect_stdout()

        async with EventBuffer(
            storage,
            queue_max=settings.queue_max,
            batch_size=settings.batch_size,
            batch_interval_s=settings.batch_interval_s,
        ) as buffer:
            stop_event = asyncio.Event()
            _install_signal_handlers(stop_event)

            client_to_server = asyncio.create_task(
                _pump(
                    src=client_reader,
                    dst=child.stdin,
                    direction="client_to_server",
                    buffer=buffer,
                    session_id=session_id,
                    stop_event=stop_event,
                ),
                name="pump-c2s",
            )
            server_to_client = asyncio.create_task(
                _pump(
                    src=child.stdout,
                    dst=client_writer,
                    direction="server_to_client",
                    buffer=buffer,
                    session_id=session_id,
                    stop_event=stop_event,
                ),
                name="pump-s2c",
            )
            stop_waiter = asyncio.create_task(stop_event.wait(), name="stop-waiter")

            done, pending = await asyncio.wait(
                {client_to_server, server_to_client, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    logger.error("pump task %s failed: %r", task.get_name(), exc)

            stop_event.set()
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

            with suppress(ProcessLookupError):
                if child.returncode is None:
                    if child.stdin is not None and not child.stdin.is_closing():
                        child.stdin.close()
                    try:
                        await asyncio.wait_for(child.wait(), timeout=2.0)
                    except TimeoutError:
                        child.terminate()
                        try:
                            await asyncio.wait_for(child.wait(), timeout=2.0)
                        except TimeoutError:
                            child.kill()
                            await child.wait()

            exit_code = child.returncode if child.returncode is not None else -1
            dropped = buffer.dropped

        await storage.end_session(session_id, exit_code=exit_code)
        return ProxyResult(exit_code=exit_code, events_dropped=dropped)
    finally:
        await storage.close()


async def _pump(
    *,
    src: asyncio.StreamReader,
    dst: asyncio.StreamWriter,
    direction: str,
    buffer: EventBuffer,
    session_id: int,
    stop_event: asyncio.Event,
) -> None:
    """Copy newline-delimited frames from ``src`` to ``dst``, logging each one.

    direction is "client_to_server" or "server_to_client" — we trust the
    caller, the EventRecord layer normalises.
    """
    try:
        while not stop_event.is_set():
            try:
                line = await src.readline()
            except asyncio.LimitOverrunError as exc:
                # Frame larger than our limit. Drain it as raw without parsing
                # so we still forward the bytes; otherwise the protocol stalls.
                consumed = await src.readexactly(exc.consumed)
                _record_raw(
                    buffer,
                    session_id=session_id,
                    direction=direction,
                    raw=consumed.decode("utf-8", errors="replace"),
                    note="line_limit_exceeded",
                )
                _safe_write(dst, consumed)
                continue
            except (ConnectionResetError, BrokenPipeError):
                return

            if not line:
                return  # EOF

            _safe_write(dst, line)
            try:
                await dst.drain()
            except (ConnectionResetError, BrokenPipeError):
                return

            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not decoded.strip():
                continue
            _log_frame(
                buffer,
                session_id=session_id,
                direction=direction,
                raw=decoded,
            )
    finally:
        if not dst.is_closing():
            dst.close()


def _safe_write(dst: asyncio.StreamWriter, data: bytes) -> None:
    try:
        dst.write(data)
    except (ConnectionResetError, BrokenPipeError):
        return


def _log_frame(
    buffer: EventBuffer,
    *,
    session_id: int,
    direction: str,
    raw: str,
) -> None:
    members = split_batch(raw)
    direction_lit = _direction_lit(direction)
    for member in members:
        parsed, kind = parse_frame(member)
        record = EventRecord.from_parsed(
            session_id=session_id,
            direction=direction_lit,
            parsed=parsed,
            kind=kind,
            raw=member,
        )
        buffer.record(record)


def _record_raw(
    buffer: EventBuffer,
    *,
    session_id: int,
    direction: str,
    raw: str,
    note: str,
) -> None:
    direction_lit = _direction_lit(direction)
    record = EventRecord(
        session_id=session_id,
        direction=direction_lit,
        kind="raw",
        raw=raw,
        note=note,
    )
    buffer.record(record)


def _direction_lit(direction: str) -> Direction:
    if direction in ("client_to_server", "server_to_client"):
        return cast(Direction, direction)
    raise ValueError(f"unknown direction {direction!r}")


async def _connect_stdin(limit: int) -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit, loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    return reader


async def _connect_stdout() -> asyncio.StreamWriter:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(loop=loop),
        sys.stdout.buffer,
    )
    return asyncio.StreamWriter(transport, protocol, None, loop)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            # NotImplementedError on Windows; RuntimeError if not in main thread.
            loop.add_signal_handler(sig, stop_event.set)


async def _patch_session_with_server_pid(
    storage: Storage, session_id: int, server_pid: int | None
) -> None:
    if server_pid is None:
        return
    await storage.set_server_pid(session_id, server_pid)
