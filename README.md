# mcp-firewall

> A prompt-injection firewall and audit log for [Model Context Protocol](https://modelcontextprotocol.io) (MCP) servers.

**Status: alpha — Week 1 milestone.** This release ships the proxy and audit log only. Detection and policy enforcement land in the next milestones.

## What it does

`mcp-firewall` sits between an MCP client (Claude Desktop, Cursor, Continue, …) and an MCP server (filesystem, github, postgres, …). It transparently forwards JSON-RPC traffic over stdio, while persisting every message to a local SQLite database so you can audit what tools were called with which arguments — and, in later milestones, block prompt-injection payloads before they reach the model.

```
┌──────────────┐    stdio JSON-RPC    ┌──────────────┐    stdio JSON-RPC    ┌──────────────┐
│   Claude     │ ───────────────────► │ mcp-firewall │ ───────────────────► │  MCP server  │
│   Desktop    │ ◄─────────────────── │   (proxy)    │ ◄─────────────────── │ (filesystem) │
└──────────────┘                      └──────┬───────┘                      └──────────────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │  SQLite log  │
                                      └──────────────┘
```

## Quick start

```bash
pip install -e ".[dev]"

# Run the proxy directly to see traffic
mcp-firewall run --server "npx -y @modelcontextprotocol/server-filesystem /tmp"
```

Then in another terminal:

```bash
mcp-firewall logs --tail 50           # last 50 events as a table
mcp-firewall logs --follow            # tail -f style live stream
```

## Wire it up with Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and wrap any MCP server you want to monitor:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "mcp-firewall",
      "args": [
        "run",
        "--server",
        "npx -y @modelcontextprotocol/server-filesystem /Users/me/Documents"
      ]
    }
  }
}
```

Restart Claude Desktop. The firewall will log every tool call to `data/log.db` inside the project (configurable via `--db-path` or `MCP_FIREWALL_DB`).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

## Roadmap

| Week | Milestone |
|------|-----------|
| 1    | stdio proxy + audit log (this release) |
| 2    | Rules-based + LLM detector for prompt-injection in tool results |
| 3    | OSS launch, packaging, Claude Desktop integration guide |
| 4-6  | Community rule set, HTTP/SSE transport |
| 7-9  | Pro tier: hosted logs, threat feed, alerts |
