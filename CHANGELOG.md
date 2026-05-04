# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-04

### Added

- Initial Week-1 release.
- `mcp-firewall run --server "..."` — stdio proxy that launches an MCP
  server as a subprocess via `asyncio.create_subprocess_exec` (argv form,
  no shell) and forwards JSON-RPC traffic in both directions.
- `mcp-firewall logs [--tail N | --follow]` — Rich-table viewer over the
  audit log with coloured direction arrows, kind highlighting, and JSON
  payload compaction.
- SQLite-backed audit log (WAL + `synchronous=NORMAL`), batched writes
  through an `asyncio.Queue` + background writer so DB latency cannot
  back-pressure JSON-RPC traffic.
- pydantic v2 models for JSON-RPC `request` / `response` / `notification`
  / `error`, with best-effort `parse_frame` and JSON-RPC batch splitting.
- Three ADRs documenting load-bearing decisions (stdio proxy, queue-based
  writer, event-log schema).
- GitHub Actions CI: ruff, ruff format, mypy strict, pytest on Python
  3.11 and 3.12, plus a separate `pip-audit` job.
- 27 pytest cases including an end-to-end test that spawns the real CLI
  as a subprocess and asserts a full round-trip through `cat`.

[Unreleased]: https://github.com/churik/mcp-firewall/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/churik/mcp-firewall/releases/tag/v0.1.0
