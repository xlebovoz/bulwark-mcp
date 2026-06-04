"""Tests for the capability filter (name-based tool allowlist).

Three layers of coverage:

1. **Unit** — :meth:`CapabilityFilter.check` decision/reason semantics,
   exact-match (no prefix, no case fold), and namespacing.
2. **Config** — the YAML ``capability:`` section parses, validates, rejects
   malformed ``<server>.<tool>`` names, and collapses duplicates.
3. **Proxy integration** — the real CLI in a subprocess (mirrors
   ``tests/test_proxy_block.py``): a not-allowlisted ``tools/call`` gets a
   ``-32603`` error, never reaches the server, and is audited as
   ``blocked_by_capability``; ``tools/list`` is untouched; capability
   composes with — and never overrides — the rules layer; and an empty
   allowlist emits the documented fail-open startup warning.

The LLM classifier is never involved; everything here is deterministic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

from bulwark_mcp.capability import CapabilityFilter, CapabilitySettings
from bulwark_mcp.config import resolve_settings
from bulwark_mcp.storage import Storage

# ---------------------------------------------------------------------
# Unit: CapabilityFilter.check
# ---------------------------------------------------------------------


class TestCapabilityCheck:
    def test_empty_allowlist_is_fail_open(self) -> None:
        f = CapabilityFilter(CapabilitySettings())
        decision = f.check("filesystem.read")
        assert decision.allowed is True
        assert decision.reason == "no_allowlist"
        assert f.active is False

    def test_tool_present_is_allowed(self) -> None:
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("filesystem.read",)))
        decision = f.check("filesystem.read")
        assert decision.allowed is True
        assert decision.reason == "in_allowlist"
        assert f.active is True

    def test_tool_absent_is_blocked(self) -> None:
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("filesystem.read",)))
        decision = f.check("filesystem.write")
        assert decision.allowed is False
        assert decision.reason == "not_in_allowlist"

    def test_match_is_exact_not_prefix(self) -> None:
        # filesystem.read must NOT match filesystem.read_file — no substring
        # or prefix matching is permitted.
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("filesystem.read",)))
        assert f.check("filesystem.read_file").allowed is False
        assert f.check("filesystem.read_file").reason == "not_in_allowlist"

    def test_match_is_case_sensitive(self) -> None:
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("filesystem.read",)))
        assert f.check("filesystem.READ").allowed is False

    def test_namespaced_prepends_server_name(self) -> None:
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("fs.read",), server_name="fs"))
        assert f.namespaced("read") == "fs.read"
        assert f.check(f.namespaced("read")).reason == "in_allowlist"

    def test_namespaced_without_server_name_is_bare(self) -> None:
        f = CapabilityFilter(CapabilitySettings(allowed_tools=("fs.read",)))
        assert f.namespaced("read") == "read"


# ---------------------------------------------------------------------
# Config: the capability: YAML section
# ---------------------------------------------------------------------


class TestCapabilityConfig:
    def test_missing_section_is_empty_allowlist(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text("storage:\n  db_path: x.db\n", encoding="utf-8")
        settings = resolve_settings(cli_config=cfg)
        assert settings.capability.allowed_tools == ()
        assert settings.capability.server_name == ""

    def test_valid_section_parses(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {"capability": {"server_name": "fs", "allowed_tools": ["fs.read", "fs.write"]}}
            ),
            encoding="utf-8",
        )
        settings = resolve_settings(cli_config=cfg)
        assert settings.capability.server_name == "fs"
        assert settings.capability.allowed_tools == ("fs.read", "fs.write")

    def test_duplicates_collapsed(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            yaml.safe_dump({"capability": {"allowed_tools": ["fs.read", "fs.read", "fs.write"]}}),
            encoding="utf-8",
        )
        settings = resolve_settings(cli_config=cfg)
        assert settings.capability.allowed_tools == ("fs.read", "fs.write")

    @pytest.mark.parametrize(
        "bad_name",
        [
            pytest.param("nodot", id="no_dot"),
            pytest.param(".read", id="empty_server"),
            pytest.param("fs.", id="empty_tool"),
            pytest.param("fs .read", id="whitespace"),
            pytest.param("a.b.c", id="multi_dot"),
        ],
    )
    def test_malformed_tool_name_rejected(self, tmp_path: Path, bad_name: str) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            yaml.safe_dump({"capability": {"allowed_tools": [bad_name]}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="not a valid"):
            resolve_settings(cli_config=cfg)

    def test_allowed_tools_must_be_a_list(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text("capability:\n  allowed_tools: 'fs.read'\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a list"):
            resolve_settings(cli_config=cfg)


# ---------------------------------------------------------------------
# Proxy integration (real CLI subprocess; mirrors test_proxy_block.py)
# ---------------------------------------------------------------------


async def _run_proxy_subprocess(
    *,
    db_path: Path,
    config_path: Path,
    server_cmd: str,
    frames: list[str],
    timeout: float = 8.0,
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bulwark_mcp",
        "run",
        "--server",
        server_cmd,
        "--db-path",
        str(db_path),
        "--config",
        str(config_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    payload = "\n".join(frames).encode() + b"\n"
    proc.stdin.write(payload)
    await proc.stdin.drain()
    proc.stdin.close()
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        pytest.fail("proxy did not exit within the timeout")
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


def _write_capability_config(
    path: Path,
    *,
    allowed_tools: list[str],
    server_name: str = "testserver",
    detector: bool = False,
) -> None:
    cfg: dict[str, object] = {
        "capability": {"server_name": server_name, "allowed_tools": allowed_tools},
    }
    if detector:
        cfg["detector"] = {"enabled": True, "llm": {"enabled": False}, "max_latency_ms": 200}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _tools_call(*, frame_id: int, name: str, arguments: dict[str, object]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": frame_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        separators=(",", ":"),
    )


async def test_c2s_tool_not_in_allowlist_is_blocked(tmp_path: Path) -> None:
    db = tmp_path / "log.db"
    cfg = tmp_path / "cfg.yaml"
    _write_capability_config(cfg, allowed_tools=["testserver.allowed_tool"])

    frame = _tools_call(frame_id=11, name="blocked_tool", arguments={"path": "/etc/passwd"})
    rc, stdout, _stderr = await _run_proxy_subprocess(
        db_path=db, config_path=cfg, server_cmd="cat", frames=[frame]
    )
    assert rc == 0

    # The client must see exactly one line: a -32603 capability error.
    # `cat` echoing the request would add a second line — its absence proves
    # the frame was never forwarded to the server.
    out_lines = [line for line in stdout.decode().splitlines() if line.strip()]
    assert len(out_lines) == 1
    received = json.loads(out_lines[0])
    assert received["id"] == 11
    assert received["error"]["code"] == -32603
    assert "testserver.blocked_tool" in received["error"]["message"]
    assert "allowlist" in received["error"]["message"]
    assert "result" not in received  # never an echo of the original call

    # Audit log: a blocked_by_capability row with the tool name, a trace id,
    # and the truncated arguments.
    async with Storage(db) as storage:
        rows = await storage.latest_events(limit=10)
    cap_rows = [
        r
        for r in rows
        if r["direction"] == "client_to_server"
        and (r["note"] or "").startswith("blocked_by_capability")
    ]
    assert len(cap_rows) == 1
    row = cap_rows[0]
    assert "tool=testserver.blocked_tool" in row["note"]
    assert "trace=" in row["note"]
    assert row["method"] == "tools/call"
    assert row["params_json"] is not None
    assert "/etc/passwd" in row["params_json"]


async def test_tools_list_is_not_filtered(tmp_path: Path) -> None:
    db = tmp_path / "log.db"
    cfg = tmp_path / "cfg.yaml"
    _write_capability_config(cfg, allowed_tools=["testserver.allowed_tool"])

    # tools/list carries no params.name, so capability does not apply — the
    # frame is forwarded and `cat` echoes it back verbatim.
    frame = json.dumps(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
        separators=(",", ":"),
    )
    rc, stdout, _stderr = await _run_proxy_subprocess(
        db_path=db, config_path=cfg, server_cmd="cat", frames=[frame]
    )
    assert rc == 0
    out_lines = [line for line in stdout.decode().splitlines() if line.strip()]
    assert len(out_lines) == 1
    received = json.loads(out_lines[0])
    assert "error" not in received
    assert received == json.loads(frame)  # untouched echo


async def test_capability_passes_then_rules_still_block(tmp_path: Path) -> None:
    """Layer-independence: an allowlisted tool whose arguments carry a
    shell-injection payload passes capability but is still blocked by the
    rules layer — capability does not override (or suppress) rules."""
    db = tmp_path / "log.db"
    cfg = tmp_path / "cfg.yaml"
    _write_capability_config(cfg, allowed_tools=["testserver.allowed_tool"], detector=True)

    frame = _tools_call(
        frame_id=9,
        name="allowed_tool",
        arguments={"cmd": "rm -rf --no-preserve-root /"},
    )
    rc, stdout, _stderr = await _run_proxy_subprocess(
        db_path=db, config_path=cfg, server_cmd="cat", frames=[frame]
    )
    assert rc == 0
    out_lines = [line for line in stdout.decode().splitlines() if line.strip()]
    assert len(out_lines) == 1
    received = json.loads(out_lines[0])
    assert received["id"] == 9
    # A RULES block is -32099, not the capability filter's -32603.
    assert received["error"]["code"] == -32099
    assert "blocked by bulwark-mcp" in received["error"]["message"]

    async with Storage(db) as storage:
        rows = await storage.latest_events(limit=10)
    # Capability did not block — no blocked_by_capability row.
    assert not [r for r in rows if (r["note"] or "").startswith("blocked_by_capability")]
    # The rules layer did — the c2s row is a detector block.
    c2s = [r for r in rows if r["direction"] == "client_to_server"]
    assert any(r["det_action"] == "block" for r in c2s)


async def test_empty_allowlist_emits_fail_open_warning(tmp_path: Path) -> None:
    db = tmp_path / "log.db"
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("capability:\n  allowed_tools: []\n", encoding="utf-8")

    frame = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        separators=(",", ":"),
    )
    rc, _stdout, stderr = await _run_proxy_subprocess(
        db_path=db, config_path=cfg, server_cmd="cat", frames=[frame]
    )
    assert rc == 0
    # Decision 2: never block silently when unconfigured — warn loudly.
    assert "capability filter inactive" in stderr.decode()
