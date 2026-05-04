# mcp-firewall

> A prompt-injection firewall and audit log for [Model Context Protocol](https://modelcontextprotocol.io) (MCP) servers.

[![CI](https://github.com/churik/mcp-firewall/actions/workflows/ci.yml/badge.svg)](https://github.com/churik/mcp-firewall/actions/workflows/ci.yml)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

> **Status: Week-1 alpha.** Today the proxy and the audit log work end-to-end; detection and policy enforcement land in the next milestones. See the [roadmap](#roadmap).

## What it does

`mcp-firewall` sits between an MCP client (Claude Desktop, Cursor, Continue, …) and an MCP server (filesystem, github, postgres, …). It transparently forwards JSON-RPC traffic over stdio, while persisting **every** message — both directions — to a local SQLite database. You can audit which tools your model actually called, with which arguments, and (in milestone 2) block prompt-injection payloads inside tool *results* before they ever reach the model.

```
                  ┌──────────────┐    stdio JSON-RPC
                  │   Claude     │
                  │   Desktop    │
                  └──────┬───────┘
                         │ launches as a subprocess
                         ▼
   ┌──────────────────────────────────────────────────┐
   │              mcp-firewall (proxy)                │
   │                                                  │
   │   ┌──────────┐    ┌──────────┐    ┌──────────┐  │
   │   │  pump    │───▶│  parse   │───▶│  audit   │  │
   │   │  c2s     │    │  & log   │    │  buffer  │  │
   │   └──────────┘    └──────────┘    └────┬─────┘  │
   │   ┌──────────┐    ┌──────────┐         │        │
   │   │  pump    │◀───│  parse   │◀────────┘        │
   │   │  s2c     │    │  & log   │   (asyncio.Queue │
   │   └──────────┘    └──────────┘    + bg writer)  │
   └────────┬─────────────────────────────────┬──────┘
            │ stdio                           │ aiosqlite
            ▼                                 ▼
    ┌──────────────┐                  ┌──────────────┐
    │  MCP server  │                  │  SQLite log  │
    │ (subprocess) │                  │ (data/log.db)│
    └──────────────┘                  └──────────────┘
```

## Features (Week 1)

- 🔌 **Drop-in proxy** — your MCP client talks to `mcp-firewall`; `mcp-firewall` talks to the real server. No protocol changes.
- 📝 **Append-only audit log** — every JSON-RPC frame in both directions, persisted to SQLite (WAL mode, batched writes).
- 🧱 **Crash-safe** — `synchronous=NORMAL` + WAL keeps logs durable across crashes; queue-based writer keeps the data path lock-free.
- 🛡️ **Safe argv handling** — the underlying server is launched with `subprocess_exec` (no shell), so a crafted `--server` string can't shell-inject.
- 📜 **Rich viewer** — `mcp-firewall logs --tail` and `--follow` give a colourised table with direction arrows, kind highlighting, and JSON-collapsed payloads.
- 🚫 **Never corrupts the protocol** — frames over the line limit are forwarded byte-for-byte and logged as `raw`; malformed JSON is logged as `parse_error` without dropping subsequent traffic.

## Quick start

```bash
git clone https://github.com/churik/mcp-firewall.git
cd mcp-firewall
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Sanity check
mcp-firewall --version

# End-to-end smoke test using `cat` as a fake echo server
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
| mcp-firewall run --server "cat"

# Inspect what was captured
mcp-firewall logs --tail 20
```

You should see the two outbound frames echoed back through stdout, and four rows in the audit log: two `client_to_server` and two `server_to_client`.

## Wire it up with Claude Desktop

Open `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) and wrap any MCP server you want to monitor:

```json
{
  "mcpServers": {
    "filesystem-monitored": {
      "command": "/absolute/path/to/.venv/bin/mcp-firewall",
      "args": [
        "run",
        "--server",
        "npx -y @modelcontextprotocol/server-filesystem /Users/me/Documents",
        "--db-path",
        "/Users/me/.local/state/mcp-firewall/log.db"
      ]
    }
  }
}
```

> ⚠️ Use the **absolute** path to the `mcp-firewall` binary (e.g. inside your venv's `bin/`), because Claude Desktop does not inherit your shell's `PATH`.

Restart Claude Desktop. From a separate terminal:

```bash
mcp-firewall logs --follow --db-path ~/.local/state/mcp-firewall/log.db
```

Now ask the model to do something with your filesystem — every tool call appears in the table in real time.

### Cursor / other MCP clients

Any client that launches an MCP server as a subprocess works the same way. Replace the original `command`/`args` of the MCP server with `mcp-firewall run --server "<original command>"`.

## Configuration

Precedence (high → low): **CLI flag → environment variable → YAML file → built-in default**.

| Setting              | CLI flag        | Env var               | YAML key                       | Default                  |
|----------------------|-----------------|-----------------------|--------------------------------|--------------------------|
| Audit DB location    | `--db-path`     | `MCP_FIREWALL_DB`     | `storage.db_path`              | `<project>/data/log.db`  |
| Config file path     | `--config`      | `MCP_FIREWALL_CONFIG` | —                              | none                     |
| Queue overflow limit | —               | —                     | `storage.queue_max`            | `10000`                  |
| Batch size           | —               | —                     | `storage.batch_size`           | `100`                    |
| Batch interval       | —               | —                     | `storage.batch_interval_ms`    | `50`                     |

See [`config.example.yaml`](config.example.yaml) for a working template.

## Repository layout

```
mcp-firewall/
├── src/mcp_firewall/
│   ├── __init__.py
│   ├── __main__.py        # `python -m mcp_firewall`
│   ├── cli.py             # click-based CLI: `run`, `logs`
│   ├── config.py          # CLI/env/YAML resolution
│   ├── models.py          # JSON-RPC 2.0 parser + EventRecord
│   ├── proxy.py           # stdio proxy with subprocess child
│   └── storage.py         # SQLite + queue-based async writer
├── tests/                 # pytest, 27 cases as of Week 1
├── docs/adr/              # architecture decision records
│   ├── 0001-stdio-proxy-via-asyncio-subprocess.md
│   ├── 0002-sqlite-with-async-queue-writer.md
│   └── 0003-event-log-schema.md
├── .github/workflows/ci.yml
├── pyproject.toml         # hatchling, pinned major versions
└── data/                  # default DB location (gitignored)
```

## Development

```bash
# Lint, format-check, type-check, test
ruff check .
ruff format --check .
mypy src/ tests/
pytest -q

# One-liner sanity check (mirrors what CI runs):
ruff check . && ruff format --check . && mypy src/ tests/ && pytest -q
```

The test suite spawns a real `python -m mcp_firewall run --server "cat"` subprocess to verify the round-trip, so you don't need a real MCP server installed to develop.

### How decisions get made

Architecture decisions land as ADRs in `docs/adr/`. Three ADRs ship with Week 1; the next milestones will add:

- ADR-0004: detector pipeline (rules → LLM-classifier).
- ADR-0005: HTTP/SSE transport.
- ADR-0006: Pro tier — hosted log shipping & threat-feed sync.

## Roadmap

| Milestone | Status | Scope                                                                |
|-----------|--------|----------------------------------------------------------------------|
| Week 1    | ✅     | stdio proxy + audit log + CLI viewer (this release)                  |
| Week 2    | 🚧     | Rules-based + LLM detector for prompt-injection in tool results      |
| Week 3    | ⏳     | OSS launch + packaging on PyPI + Claude Desktop integration guide    |
| Week 4-6  | ⏳     | Community rules repo, HTTP/SSE transport, viewer filters             |
| Week 7-9  | ⏳     | Pro tier: hosted logs, threat feed, Slack/Discord/Telegram alerts    |
| Week 10-12| ⏳     | First paying users — pricing & monetisation                          |

## License

[AGPL-3.0-or-later](LICENSE). Why AGPL? Because a hosted competitor cannot take this code, run it as a service, and keep their improvements proprietary — improvements have to flow back to the community. The CLI itself stays as free as ever.

## Contributing

Issues and PRs welcome. Two house rules:

1. **Conventional commits** (`feat:`, `fix:`, `docs:`, …). The CI lints them.
2. **Tests for behaviour, not for implementation.** If a refactor leaves the API unchanged, the existing tests must still pass.

If you find a real-world prompt-injection PoC that `mcp-firewall` doesn't catch, please open an issue with a reproduction. That's the most valuable contribution you can make right now.
