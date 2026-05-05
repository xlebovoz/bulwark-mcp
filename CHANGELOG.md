# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-05-05

### Added

- **Community readiness.**
  - `CONTRIBUTING.md` — setup, rule-pack authoring, the **promotion ladder** (community → built-in: basic lint vs strict + 2 tests).
  - `SECURITY.md` — disclosure flow via GitHub Security Advisories.
  - (CODE_OF_CONDUCT, issue/PR templates, labels — deferred to manual.)
- **`mcp-firewall stats`** — local-only summary of the audit log: verdict
  counts, top-5 rules, latency p50/p95. Rich table by default, versioned
  JSON via `--json`. `--since 7d|24h|30m` window selector.
- **`mcp-firewall rules lint [--strict]`** — validate community-contributed
  YAML packs. Strict mode enforces `severity_tier`, `attack_examples`,
  HTTP(S) `source` URL, sufficient-length `description`, and that
  attack-examples actually match the regex.
- **Loopback health endpoint** — `mcp-firewall run --health-port N` binds
  `127.0.0.1:N` and serves `GET /health` returning JSON status. For
  k8s/docker liveness probes. Loopback-only by design, no auth, no TLS.
- **Opt-in anonymous telemetry** — `MCP_FIREWALL_TELEMETRY=true` enables
  a daily anonymous payload (version, OS, Python version, event counts).
  **No rule names, no traffic content, no fingerprinting.**
  - `MCP_FIREWALL_TELEMETRY_URL` env override.
  - `MCP_FIREWALL_TELEMETRY_URL=disabled` kill-switch (still writes local log).
  - First-run banner in stderr, single-shot per process.
  - Local log at `<db-dir>/telemetry.log`, written before each HTTP call.
  - `installation_id` UUID at `<db-dir>/installation_id`, deletable to reset.
  - Silent fail on network errors.
  - Files written with mode `0600`.
- **Integration test fixtures** for `github`, `brave-search`, `postgres` MCP
  servers — smoke + benign + attack scenarios per server, with no real
  API calls.
- `docs/OBSERVABILITY.md` — full telemetry schema + privacy contract.
- `docs/INTEGRATIONS.md` — table of tested MCP servers + config snippets.
- `docs/AUDIT-REPORT-week3.md` — adversarial review findings (10 total;
  4 fixed in this milestone, 6 deferred to v0.4 with explicit plans).
- ADR-0005 — observability and telemetry privacy.

### Changed

- **Detection layer (audit-fix carry-overs from v0.2):**
  - Three-pass scan in `RulesEngine.detect`: raw + within-word-normalised
    (NFKC + invisible-char strip) + between-word-normalised (NFKC +
    invisibles → space + whitespace collapse). Catches per-word zero-
    width insertion and full-width Latin substitutions that v0.2 missed.
  - JSON-RPC batch frames are now inspected **per member**. The audit
    log records per-member verdicts; if any member blocks, the whole
    batch is replaced with a 1-element JSON-RPC batch reply (valid
    array shape).
  - Non-text content (image/resource blocks in `result.content`) now
    surfaces `note=skipped:non_text_content` so operators can see
    binary content was forwarded uninspected.
  - LLM classifier truncation is now one-end (head-only) — closes the
    seam-evasion path of v0.2's middle-truncation. Frames over the
    truncation threshold get `note=ok:truncated=<chars>` in audit.

### Security

- `platform.release()` is now reduced to its first numeric component
  before telemetry transmission — full kernel build strings (e.g.
  `5.4.0-foo-bar`) were uniquely identifying custom kernels. Privacy
  contract restored.
- `data/telemetry.log` and `data/installation_id` are created with
  mode `0600` so co-tenants on a shared machine cannot read payloads.
- `_trace_id` no longer mixes the entire raw frame into the SHA1 seed
  — `os.urandom(8)` already provides unguessability and the previous
  approach hashed up to 8 MiB on the hot block path.
- Truncation events are now visible in audit via `note=ok:truncated=N`.

### Known limitations (v0.4 backlog)

- ReDoS via community-contributed regex (no pattern-time budget yet) —
  zero community packs ship in v0.3, so the threat is theoretical
  until first PR lands.
- JSON-RPC batch block emits a 1-element reply array; non-blocking
  members lose their `id` correlation. Per-id error synthesis tracked
  for v0.4.
- Health endpoint slowloris exposure (loopback-only mitigates blast
  radius) — `wait_for(timeout=5s)` + StreamReader byte cap planned.
- Cross-script homoglyphs (Cyrillic look-alikes for Latin) still
  bypass the detector — handling needs a confusables table (~10 MB).
- Stats / telemetry build a 64 KB cap on `det_rules` JSON-parse to
  avoid pathological-row CPU spikes.

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

[Unreleased]: https://github.com/churik5/mcp-firewall/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/churik5/mcp-firewall/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/churik5/mcp-firewall/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/churik5/mcp-firewall/releases/tag/v0.1.0
