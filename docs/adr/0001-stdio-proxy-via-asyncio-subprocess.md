# ADR-0001: stdio proxy via `asyncio.subprocess`

- **Status:** accepted
- **Date:** 2026-05-04
- **Deciders:** @churik

## Context

The Model Context Protocol (MCP) defines two transports: **stdio** (newline-delimited JSON-RPC) and **HTTP+SSE**. In practice, Claude Desktop and most desktop clients launch MCP servers as a child process and talk over stdio. We want a proxy that:

1. Is invisible to both the client and the server (no protocol changes).
2. Logs every JSON-RPC frame in both directions.
3. Adds <50 ms overhead per message (no LLM calls in the hot path for v0).
4. Survives partial frames and ill-formed JSON without dropping the stream.

## Decision

`mcp-firewall run --server "<cmd>"` is invoked by the client *in place of* the real MCP server. Internally it:

- Launches `<cmd>` as a child process via `asyncio.create_subprocess_exec` (argv list, **no shell**).
- Spawns two coroutines: `pump_client_to_server` and `pump_server_to_client`, each reading newline-delimited frames from one side and writing to the other.
- After (or before — order does not matter for correctness) forwarding, each frame is parsed best-effort into a Pydantic model and pushed onto an `asyncio.Queue`. A third coroutine drains the queue into SQLite.
- On EOF from either side, the proxy flushes the queue and exits with the child's return code.

## Consequences

**Positive**

- Zero-config for users: it looks like an MCP server to Claude Desktop, looks like an MCP client to the real server.
- Log writes never block the data path — a slow disk delays *logging*, not tool calls.
- `subprocess_exec` (argv form) sidesteps shell-injection from `--server` if a user pastes untrusted text. The user opts into shell parsing only by pre-splitting the command via `shlex.split`.

**Negative / accepted trade-offs**

- Logs may lag behind reality by up to one queue flush (~10 ms). Acceptable for an audit log; not acceptable for a real-time enforcement gate (deferred to ADR-0004).
- We can lose at most ~queue-size events on hard kill (`kill -9`). On normal shutdown we drain.
- We currently *do not* parse stderr of the child — only stdin/stdout. Stderr is forwarded transparently to ours so it surfaces in Claude Desktop's log panel.

## Alternatives considered

- **Bash shim**: a wrapper `tee`-ing both directions to log files. Rejected — cannot parse JSON-RPC, no structured data for the viewer or future detectors.
- **TCP proxy**: requires Claude Desktop to speak TCP-MCP, which only a minority of clients support today.
- **Synchronous threads + `selectors`**: works but doubles the lines-of-code and pessimises latency on macOS where `select()` on tty pipes is finicky.
