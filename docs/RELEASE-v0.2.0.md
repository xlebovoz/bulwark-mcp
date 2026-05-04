# mcp-firewall v0.2.0 — Detection Layer

**Date:** 2026-05-04
**Status:** alpha — opt-in detector; Week 1 audit-only behaviour preserved when detector is off.

## Headline

`mcp-firewall` now enforces, not just observes. Turn on the detector and every JSON-RPC frame goes through a rules + local-LLM cascade, with high-confidence prompt-injection payloads in tool results replaced by a sanitised `isError: true` reply before the agent ever sees them. The original bytes stay in the audit log for forensics. Off by default, so existing Week 1 users keep their latency profile until they opt in.

```bash
ollama pull qwen2.5:3b   # optional — rules-only mode works without it
mcp-firewall run --server "..." --detector
mcp-firewall logs --verdict BLOCK --tail 50
```

## What's new

### The detection cascade (ADR-0004)

1. **`RulesEngine`** — 24+ regex signatures shipped as YAML packs. Catalogued in `docs/THREATS.md` with source URLs (garak, promptfoo, Trojan Source, embracethered, MITRE ATT&CK).
2. **`OllamaClassifier`** — local LLM verdict via `qwen2.5:3b`. SHA-256 cache, circuit breaker (3 failures → 60 s open), hard 1 s per-request timeout.
3. **`Policy`** — YAML-driven first-match rule engine. Default action `allow`; built-in rules block on score ≥ 0.85 and warn on bare classifier signal. **Custom policies welcome** — drop a YAML and pass `--policies <path>`.
4. **`Inspector`** — orchestrator. Hard latency abort at 1.25× `max_latency_ms` falls back to `WARN` so a slow Ollama can never wedge the pump.

### CLI

| Command | What it does |
|---|---|
| `mcp-firewall run --detector` | Run the proxy with the detection layer on. |
| `mcp-firewall run --policies <path>` | Override the built-in policy. |
| `mcp-firewall detect "<text>"` | Run the cascade over a single string. Exit 0 = PASS, 1 otherwise. |
| `mcp-firewall logs --verdict BLOCK` | Filter the audit log to blocked frames. |

### Storage

Schema bumped from v1 to v2:

- `events` gains six `det_*` columns (verdict, score, rules, classifier, latency, action).
- New `classifier_cache` table for the SHA-256 verdict cache.
- Partial-failure-safe migration via `BEGIN IMMEDIATE` and idempotent `ALTER TABLE`.

A Week-1 `log.db` opens cleanly under v0.2.0 — the migration runs once on first open and writes a new `schema_version=2` row alongside the existing `1`.

### Performance budget

ADR-0004 §7 numbers, validated by `tests/test_perf.py` and against a real Ollama:

| Path                                | Budget       | Measured (M-series Mac) |
|-------------------------------------|--------------|--------------------------|
| Rules detector                      | ≤5 ms p95    | 0.04 ms p95              |
| Inspector cache hit                 | ≤10 ms p95   | 0.13 ms p95              |
| Inspector with LLM (cache miss)     | ≤200 ms p95  | ~146 ms p50, ≤163 ms p95 |
| Hard inspector abort threshold      | 250 ms       | enforced in code         |

Cold Ollama call (~1.8 s on first model load) busts the p95 budget — handled by the hard-abort path: that one frame returns `WARN` instead of stalling the pump, then subsequent frames stay under budget. Warm-up tip in `docs/RUNBOOK.md`.

## Test coverage

`pytest tests/` jumps from **27 cases (Week 1)** to **121 cases (Week 2)**, all green. Highlights:

- `test_proxy_block.py` runs the **real CLI** as a subprocess, feeds it a prompt-injection tool result, and asserts the agent receives the sanitised replacement (not the injection).
- `test_storage_migration.py` exercises a hand-rolled v1 DB through the live migration, including a "crashed mid-migration" recovery scenario.
- `test_detectors_rules.py` parametrises 30+ canonical PoCs from public sources and asserts each is caught by the right rule id.
- `test_perf.py` asserts the latency budgets above with real numbers.

## Self-audit findings (5 fixed in this release, 5 deferred)

Full report in `docs/AUDIT-REPORT-week2.md`. Headline:

**Fixed:**
1. Classifier prompt-injection via `>>>` / `Answer:` tokens — content is now sanitised before substitution.
2. Unknown `when:` keys in policies — now rejected at load time (a typo would otherwise silently match every frame).
3. Synthetic-block s2c row missed `det_verdict` — now propagated from the parent c2s decision.
4. `_trace_id` was predictable — now uses `os.urandom`.
5. Migration used `BEGIN` (deferred) instead of `BEGIN IMMEDIATE` — now properly serialised.

**Deferred to v0.3 (documented in `docs/THREATS.md` §"Limitations"):**

- LLM cascade only inspects `result.content[*].text` blocks; non-text shapes bypass the classifier.
- No NFKC normalisation in rules — homoglyph / per-word zero-width attacks slip through some patterns.
- Batch JSON-RPC frames inherit a single inspection verdict across all members.
- Anthropic Haiku as a third-tier fallback — Ollama-only or rules-only today.
- Truncation seam in `_truncate` can split a marker across the discard boundary.

## Compatibility

- Python ≥ 3.11.
- AGPL-3.0-or-later.
- **No new runtime dependencies.** `httpx` was added in Week 1 in anticipation of this milestone.
- Detector is **off by default**. Set `detector.enabled: true` in config or pass `--detector` to opt in.

## Migrating from v0.1.0

1. `pip install -U mcp-firewall` (or `pip install -e .` from the repo).
2. Existing `log.db` migrates automatically on first open. To verify:
   ```bash
   sqlite3 ~/path/to/log.db 'SELECT MAX(version) FROM schema_version;'   # → 2
   ```
3. To turn the detector on: add `detector.enabled: true` to your config, or pass `--detector` once on the command line.
4. Optional: pull `qwen2.5:3b` via Ollama for the LLM-classifier path. The detector works in rules-only mode without it.

## Acknowledgements

Rule signatures sourced from:

- [garak](https://github.com/leondz/garak) (probes/promptinject, probes/exploitation)
- [promptfoo](https://github.com/promptfoo/promptfoo) (templates/redteam)
- [Trojan Source](https://trojansource.codes/) (CVE-2021-42574)
- [embracethered.com](https://embracethered.com/) (markdown-image exfil, Unicode tags, conditional injection)
- [Simon Willison's prompt-injection tag](https://simonwillison.net/tags/prompt-injection/)
- [MITRE ATT&CK](https://attack.mitre.org/) (T1027.013, T1059.004, T1070.003)
- [GTFOBins](https://gtfobins.github.io/) (reverse-shell incantations)
- [jailbreakchat.com](https://www.jailbreakchat.com/) (DAN/AIM/STAN family)

Without these public catalogues this release would not exist.
