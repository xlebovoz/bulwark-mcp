# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-05-04

### Added

- **Detection layer (ADR-0004).** Optional, opt-in via `detector.enabled`.
- `mcp-firewall detect "<text>"` — manually run the cascade over a single
  string and print the verdict. Exits 0 on `PASS`, 1 otherwise.
- `mcp-firewall logs --verdict {PASS|WARN|BLOCK}` — filter the audit-log
  viewer to a specific detector verdict.
- `--detector / --no-detector` and `--policies <path>` flags on
  `mcp-firewall run`.
- `RulesEngine` with **24+ regex signatures** shipped as YAML packs in
  `src/mcp_firewall/rules/builtin/` (role hijack, exfiltration, invisible
  Unicode, HTML rendering tricks, shell injection). Sources catalogued
  per rule in `docs/THREATS.md`.
- `OllamaClassifier` — local LLM verdict via `qwen2.5:3b` over Ollama,
  with SHA-256 cache, circuit breaker (3 failures → 60 s open), and a
  hard 1 s per-request timeout.
- `Inspector` — orchestrates rules+LLM cascade, applies policy, composes
  sanitised replacement bytes on `block`. Hard latency abort at 1.25 ×
  `max_latency_ms` falls back to `WARN` so a slow Ollama can never wedge
  the pump.
- `Policy` — YAML-driven first-match rule engine with `direction`,
  `method`, `classifier`, `detector_score_at_least`, `tool_args_match_any`,
  and `rules_hit_any` clauses. Built-in default mirrors
  `config/policies.yaml`.
- Schema migration v1 → v2 with new `det_*` columns on `events` and a
  `classifier_cache` table; partial-failure-safe via `BEGIN IMMEDIATE`
  and idempotent `ALTER TABLE`.
- 94 new test cases (121 total) including:
  - end-to-end block test that runs the real CLI under `cat` and asserts
    the agent receives the sanitised replacement (not the injection);
  - perf benchmark asserting rules ≤5 ms p95 and inspector ≤10 ms p95
    on cache-hit/short-circuit paths;
  - schema migration tests including a partial-migration recovery.
- `docs/THREATS.md` — full rule catalogue with source URLs and FPR notes.
- `docs/PERF.md` — latency budget + measured numbers, real-Ollama profile.
- `docs/AUDIT-REPORT-week2.md` — adversarial self-audit with 10 findings;
  5 fixed in this milestone, 5 documented limitations tracked for v0.3.
- `docs/blocked-attack-demo.log` — canonical end-to-end attack capture.
- `config/policies.yaml` — sample committed policy.

### Changed

- `Storage.latest_events(verdict=...)` filter for the new column.
- `Settings` now has a `detector: DetectorSettings` sub-dataclass.
- `_pump` reads → inspects → forwards-or-replaces → logs (when detector
  is on); Week 1 read → forward → log shape is preserved when detector
  is off, so existing users keep their latency profile.

### Security

- The classifier prompt strips `<<<` / `>>>` / `Answer:` from
  attacker-controlled content to prevent meta-prompt injection of the
  classifier itself.
- Policy loader rejects rules with unknown `when:` keys (a typo would
  otherwise silently match every frame).
- Trace ids in synthetic block replies use `os.urandom(8)` so they
  cannot be pre-computed by an attacker probing the proxy.

### Known limitations (v0.3 backlog)

- LLM cascade only inspects `result.content[*].text` blocks; non-text
  shapes bypass the classifier (rules still scan).
- No NFKC normalisation in rules — homoglyph and 1-zero-width-per-word
  attacks evade some patterns.
- Batch JSON-RPC frames inherit a single inspection verdict across
  all members.
- Anthropic Haiku as a fallback tier is deferred — Ollama-only or
  rules-only today.

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

[Unreleased]: https://github.com/churik/mcp-firewall/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/churik/mcp-firewall/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/churik/mcp-firewall/releases/tag/v0.1.0
