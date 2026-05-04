# Runbook

Operational notes for running `mcp-firewall` in front of a real MCP server.

## End-to-end verification (smoke test)

The fastest way to confirm a fresh checkout actually works against a real MCP server. Pinned version because the latest `@modelcontextprotocol/server-filesystem` (as of 2026-05-04) ships zod v4 which trips a Node 20 ESM resolution bug — milestone-2 issue tracker entry to revisit once the upstream lands a fix.

```bash
# 1. Prepare a target directory the server can see
mkdir -p /private/tmp/mcp-fs-test
echo "hello-from-mcp-firewall" > /private/tmp/mcp-fs-test/greeting.txt

# 2. Drive the proxy with an MCP handshake + two real tool calls
{
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.1.0"}}}'
  sleep 1
  echo '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
  sleep 1
  echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_directory","arguments":{"path":"/private/tmp/mcp-fs-test"}}}'
  sleep 1
  echo '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"read_text_file","arguments":{"path":"/private/tmp/mcp-fs-test/greeting.txt"}}}'
  sleep 2
} | mcp-firewall run \
    --server "npx -y @modelcontextprotocol/server-filesystem@2025.11.25 /private/tmp/mcp-fs-test" \
    > stdout.json 2> stderr.txt

# 3. Expect: exit 0, 9 rows in the log, the greeting echoed back
mcp-firewall logs --tail 20
sqlite3 data/log.db 'SELECT COUNT(*) FROM events;'   # 9
```

If the response on `id=4` contains `hello-from-mcp-firewall`, the proxy is healthy.

> ⚠️ macOS detail: the system maps `/tmp` to `/private/tmp`. The filesystem server canonicalises paths and rejects `/tmp/...` as outside its allowed roots. Always pass the realpath when feeding paths to the server.

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
