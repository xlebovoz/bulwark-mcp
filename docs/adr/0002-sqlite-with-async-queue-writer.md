# ADR-0002: SQLite via aiosqlite with a queue-based writer

- **Status:** accepted
- **Date:** 2026-05-04
- **Deciders:** @churik

## Context

We need to durably log every MCP frame. Requirements:

1. Append-heavy, read-rare workload (like a journal).
2. No external services — `pip install` should be enough; no Postgres dependency.
3. Survives client/server crashes without corruption.
4. Cheap querying for the `logs --tail` viewer.

## Decision

- **Engine:** SQLite, accessed via `aiosqlite`.
- **Mode:** `journal_mode=WAL` + `synchronous=NORMAL`. WAL gives us concurrent reader (the viewer) + writer (the proxy) without locking; `synchronous=NORMAL` halves fsync cost while remaining crash-safe under WAL.
- **Writer pattern:** a single background coroutine drains an `asyncio.Queue[Event]` into SQLite using `executemany` per batch (max 100 events or 50 ms, whichever first). The proxy hot path only does `queue.put_nowait(event)`.
- **Default location:** `<project>/data/log.db`. Override via `--db-path` or `MCP_FIREWALL_DB` env var.

## Consequences

**Positive**

- One file, easy to ship to a customer for support: "send me your `log.db`".
- WAL gives near-zero contention between proxy (writer) and viewer (reader).
- Batched writes keep us under 1 ms/event amortised on a modern SSD.

**Negative / accepted trade-offs**

- Queue is in-memory: ~queue-size events lost on hard kill. We bound the queue (`maxsize=10_000`) so it cannot exhaust memory; on overflow we **drop with a warning** rather than block the data path. (Configurable.)
- SQLite is single-writer. Fine for one proxy instance per server. Multiple proxies pointing at the same DB are not supported (and not needed — each proxy launches one server).
- We do not (yet) rotate or compact the DB. v0 ships a `mcp-firewall logs --vacuum` command in week 2.

## Alternatives considered

- **JSONL file:** simpler, but `logs --tail` becomes O(N) and filtering by method/session is painful.
- **Postgres / DuckDB:** overkill for a desktop tool; adds an external dependency.
- **DuckDB embedded:** great for OLAP but not great for high-rate small inserts; SQLite wins here.
