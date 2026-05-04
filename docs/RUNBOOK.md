# Runbook

Operational notes for running `mcp-firewall` in front of a real MCP server.

## Day-to-day

### Start the proxy attached to Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` is your control plane. Edit the entry for the MCP server you want to monitor and replace its `command`/`args` with:

```json
"command": "/absolute/path/to/.venv/bin/mcp-firewall",
"args": [
  "run",
  "--server", "npx -y @modelcontextprotocol/server-filesystem /Users/me/Documents",
  "--db-path", "/Users/me/.local/state/mcp-firewall/log.db"
]
```

Restart Claude Desktop. There is no health endpoint — the canary is the audit log.

### Watch live traffic

```bash
mcp-firewall logs --follow --db-path /Users/me/.local/state/mcp-firewall/log.db
```

Filters by method/direction land in milestone 2. Until then, use SQLite directly:

```bash
sqlite3 ~/.local/state/mcp-firewall/log.db \
  "SELECT id, ts, direction, kind, method, msg_id FROM events ORDER BY id DESC LIMIT 50;"
```

### Inspect a specific session

```bash
sqlite3 ~/.local/state/mcp-firewall/log.db <<'SQL'
.mode column
.headers on
SELECT id, server_command, started_at, ended_at, exit_code FROM sessions ORDER BY id DESC LIMIT 5;
SQL
```

## Troubleshooting

### Claude Desktop can't find `mcp-firewall`

Symptom: the server entry shows up red in Claude Desktop's logs panel ("command not found").

Fix: Claude Desktop does not inherit your shell's `PATH`. Use the **absolute** path to the binary, e.g. `/Users/me/projects/mcp-firewall/.venv/bin/mcp-firewall`.

### The MCP server starts but its tools don't appear

Symptom: Claude says it has no tools available.

Diagnose: tail the log and look for `parse_error` rows. If the underlying server is writing non-JSON to stdout (banners, deprecation warnings, …), Claude Desktop sees invalid frames and rejects them.

```bash
mcp-firewall logs --tail 100 | grep parse_error
```

Fix: contact the server author; they should be writing JSON-only to stdout and any human text to stderr (which `mcp-firewall` forwards transparently).

### Proxy hangs on shutdown

Symptom: closing Claude Desktop leaves a `mcp-firewall` process behind.

Fix: `pkill -INT mcp-firewall` — the proxy's signal handler will close the server stdin, wait up to 10 s for replies, then escalate to `terminate` and `kill`. If a server consistently survives the kill, file an issue with the `--server` command and OS.

### Queue overflow warnings

Symptom: `event queue full — dropped N events so far; raise queue_max` on stderr.

Cause: the SQLite writer is slower than the JSON-RPC traffic. Almost always a sign that the underlying disk or filesystem is unusual (network mount, encrypted volume).

Fix: increase `storage.queue_max` (default `10000`) in `config.yaml` or move the DB to a local SSD via `--db-path /local/path/log.db`.

### "Pipe transport is only for pipes…"

Symptom: proxy aborts on startup with this `ValueError`.

Cause: the inherited stdout/stdin file descriptors don't match what asyncio's `connect_*_pipe` accepts (this can happen under exotic test runners).

Fix: nothing — `mcp-firewall` falls back to a blocking thread-pool writer automatically. If you still see this, please file an issue with the parent process command line.

## Rotation

The log is append-only and grows roughly proportional to traffic (~1 KB per tool call). For a personal workstation that's a few MB per month — not enough to bother rotating in v0.

Manual snapshot + truncate:

```bash
cp ~/.local/state/mcp-firewall/log.db ~/.local/state/mcp-firewall/log.db.$(date +%Y%m%d).bak
sqlite3 ~/.local/state/mcp-firewall/log.db <<'SQL'
DELETE FROM events WHERE ts < datetime('now', '-30 days');
VACUUM;
SQL
```

A built-in `mcp-firewall logs --vacuum` lands in milestone 2.
