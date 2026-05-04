# Week-2 Audit Report

**Date:** 2026-05-04
**Reviewer:** self-audit via Claude Code (general-purpose subagent, adversarial security brief)
**Scope:** the Week 2 delta — detection layer, policy engine, schema v1→v2 migration, classifier cache, CLI extensions. 5 new modules + 4 modified.

## TL;DR

| Check                                | Verdict | Notes                                                                            |
|--------------------------------------|---------|----------------------------------------------------------------------------------|
| `pip-audit` on resolved tree         | ✅ PASS | "No known vulnerabilities found".                                                |
| `ruff check` (strict, S+ASYNC+RUF)   | ✅ PASS | 0 errors after formatter run.                                                    |
| `mypy --strict`                      | ✅ PASS | 0 issues across 25 source files.                                                 |
| `pytest tests/` (full suite)         | ✅ PASS | 121 / 121 (was 27 in Week 1).                                                    |
| Adversarial review                   | ⚠️ WARN | 10 findings: **5 fixed in this milestone**, 5 deferred to v0.3 (documented).     |
| New deps                             | ✅ PASS | None — `httpx` was added in Week 1 in anticipation of this milestone.            |
| Conventional commits                 | (TBD)   | Validated at commit time.                                                        |

**Net verdict: ✅ ship.** All HIGH-severity findings closed in this milestone; remaining MEDIUM/LOW items are scoped, tested-in-test-suite where possible, and tracked.

---

## 1. Adversarial review — findings and dispositions

The reviewer was briefed against the threat model in ADR-0004:

- An attacker controls tool-result content flowing **server→client**.
- A malicious operator might write a `policies.yaml` that looks restrictive but is permissive in disguise.
- The proxy is a child process over anonymous pipes; it must never crash on hostile input.

### Fixed in v0.2

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | HIGH | **Classifier prompt-injection via `>>>` / `Answer:` tokens.** Attacker text inside `result.content` could close the prompt fence and pre-write the LLM's answer. | `_sanitise_for_prompt` in `detectors/llm.py` replaces `<<<`, `>>>`, and `Answer:` (any case) with placeholders before substitution. Hash + classification key uses the sanitised text. |
| 2 | HIGH | **Unknown `when:` keys silently ignored.** A typo'd or unknown key would make the rule match every frame, letting `action: allow` shadow stricter blocks. | `policy._compile_rule` now validates `when:` keys against an allowlist (`_VALID_WHEN_KEYS`) and raises with the offending keys + a pointer to ADR-0004 §6. New test `test_policy.py::test_rejects_unknown_when_keys`. |
| 3 | MEDIUM | **`_record_synthetic` dropped verdict columns.** The synthetic-block s2c reply was logged with only `note='synthetic-block'`; `logs --verdict BLOCK` would miss it. | `_record_synthetic` now takes the parent `InspectionResult` and propagates `det_verdict`, `det_score`, `det_rules`, `det_classifier`, `det_latency_ms`, `det_action`. Test `test_proxy_block.py` extended to assert `det_verdict='BLOCK'` on the synthetic row. |
| 8 | LOW | **`_trace_id` predictable.** Seed was `raw + perf_counter_ns`; an attacker who can probe the proxy could pre-compute trace ids. | Seed now also includes `os.urandom(8)`. Trace ids remain non-cryptographic correlation handles, just unguessable. |
| 9 | LOW | **`BEGIN` (deferred) instead of `BEGIN IMMEDIATE` for migration.** Two concurrently-launched proxies could both attempt the same v1→v2 migration. | Migration transaction now uses `BEGIN IMMEDIATE` so the second writer blocks until commit, then re-reads `schema_version` and skips. |

### Deferred to v0.3 (documented limitations)

| # | Severity | Finding | Why deferred | Mitigation today |
|---|---|---|---|---|
| 4 | MEDIUM | LLM cascade only inspects `result.content[*].text` blocks; non-`text` shapes (resources, images carrying instructions) bypass the classifier. | Requires a survey of MCP server emission shapes; out of scope for v0.2. | Rules detector still scans the raw frame and catches the most common patterns. |
| 5 | MEDIUM | No NFKC normalisation; homoglyphs (Cyrillic `і`) and 1-zero-width-per-word insertions evade rules. | Needs a normalisation pass + dual-rule evaluation. Worth its own ADR. | `unicode.zero_width_run` already catches 3+ ZW in a row; `unicode.tag_chars` catches the worst class (TAG chars). |
| 6 | MEDIUM | Batch frames inherit a single inspection verdict across all members. | Per-member inspection requires re-routing the pump's split-batch path. | Batch frames are rare in practice for stdio MCP traffic; documented as a known limitation in `docs/THREATS.md` §"Limitations". |
| 7 | MEDIUM | Rules apply to raw JSON line, not decoded concatenated text — JSON-escape evasion possible. | Tightly coupled to finding 5/6; better to redesign once. | Most observed payloads do not JSON-escape ASCII letters; `(?i)` patterns still hit the common shape. |
| 10 | LOW | Middle-truncation seam in `_truncate` can split a marker across the discard boundary. | Coupled to findings 5/7. | Default `max_input_chars=8000`; most realistic payloads fit. |

All deferred items are tracked in `docs/RELEASE-v0.2.0.md` under "Known limitations" and on the ADR-0004 §"Things deliberately NOT in v0.2" list.

### Reviewer's notes on what's solid

- The replacement composer (`inspector.py::_compose_block_replacement`) only interpolates `decision.message` (operator-controlled YAML) and `trace_id`. No attacker bytes leak into the synthetic reply.
- The hard-abort guard (`hard_abort_factor=1.25`) correctly downgrades to `WARN` and forwards the original — the pump cannot be wedged past 250 ms per frame.
- Forensic preservation: `_log_frame_with_verdict` always logs the original `decoded` string regardless of replacement, so the audit row's `raw` column has untainted evidence.
- Empty-`when:` + `block` is rejected at load time (the obvious "block-everything" footgun).

---

## 2. Dependency audit

```
$ python -m pip_audit --strict
No known vulnerabilities found
```

No new dependencies introduced in Week 2. The `httpx>=0.27,<1` pin from Week 1 is now actually exercised by the LLM classifier; no surface change for end users.

## 3. Static analysis

| Tool                | Result                                       |
|---------------------|----------------------------------------------|
| `ruff check src tests`        | All checks passed                  |
| `ruff format --check`         | All formatted                      |
| `mypy --strict src tests`     | 0 issues across 25 source files    |
| `pytest tests/`               | 121 / 121, 1.89 s wall-clock       |

## 4. Coverage gains over Week 1

| Surface                                | Week 1 | Week 2 |
|----------------------------------------|-------:|-------:|
| Test files                             | 3      | 8      |
| Test cases                             | 27     | 121    |
| `src/` modules                         | 6      | 12     |
| ADRs                                   | 3      | 4      |
| Lines of code (rough, `wc -l src/**/*.py`) | ~1100  | ~2200  |

## 5. Recommended next steps (post-ship)

1. **Corpus-driven evasion test** — collect ~30 known prompt-injection payloads from garak/promptfoo plus zero-width and homoglyph variants. Will surface the deferred findings 5/7/10 as test failures rather than theoretical risks.
2. **Move classifier prompt to chat-message format** (`/api/chat` instead of `/api/generate`). Closes finding 1 architecturally rather than via string-stripping; opens the door for system-prompt isolation.
3. **`mcp-firewall policy lint <file>` command** — flag unknown `when:` keys (now caught at load), shadowing rules, and rules with no enforceable `when:`. Pre-flight check for users authoring policies.

These are tracked as discrete tickets for v0.3.
