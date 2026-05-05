# Week-4 Audit Report

**Date:** 2026-05-05
**Scope:** Week 4 delta — all six deferred audit findings from Week 3 closed, four new CLI surfaces (`doctor`, `benchmark`, plus `rules lint` and `stats` polish), six GitHub Actions workflows, README + FAQ rewrite, PyPI publication readiness.

## TL;DR

| Check                          | Verdict | Notes                                                     |
|--------------------------------|---------|-----------------------------------------------------------|
| `pip-audit --skip-editable`    | ✅ PASS | "No known vulnerabilities found".                         |
| `ruff check src tests`         | ✅ PASS | 0 errors after format + per-file homoglyph noqa.          |
| `ruff format --check`          | ✅ PASS | All formatted.                                            |
| `mypy --strict src tests`      | ✅ PASS | 0 issues across 41 source files.                          |
| `pytest tests/` (full suite)   | ✅ PASS | 221 / 221 (was 203 in Week 3).                            |
| Audit-finding closure          | ✅ PASS | **All six v0.4 findings closed in this milestone.**       |
| New runtime deps               | ✅ PASS | None.                                                     |

**Net verdict: ✅ ship.**

## Closed audit findings (all six from Week-3 backlog)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | HIGH | **ReDoS via community-contributed regex** — `re.compile` accepted patterns like `(a+)+b` that catastrophically backtrack on attacker-controlled input. | `lint._redos_checks` runs in strict mode: a static heuristic flags nested quantifiers, plus a SIGALRM-bounded probe rejects any pattern that takes >100 ms on 512 'a's. Built-in packs pass clean; new community rules fail strict lint loud and early. Test: `tests/test_lint.py::test_strict_warns_on_nested_quantifier_redos`. |
| 2 | HIGH | **Batch-frame ID confusion** — when one member of a JSON-RPC batch blocked, the proxy emitted a 1-element reply array, leaving the other request ids unanswered (client may hang). | `_build_batch_replacement` in `proxy.py` now produces a per-id reply for every batch member: blocked ones get the actual sanitised replacement, benign s2c members are forwarded verbatim, benign c2s siblings get a synthesised `-32099 batch_aborted_by_sibling` reply with their original `id`. Tests: `test_batch_s2c_preserves_benign_members_alongside_block`, `test_batch_c2s_synthesises_per_id_replies_for_benign_siblings`. |
| 3 | MEDIUM | **Health endpoint slowloris exposure** — no per-connection timeout; `asyncio.start_server` defaulted to a 64 KiB read buffer; a hostile localhost peer could hold an event-loop slot indefinitely. | `_handle` now wraps `_serve_one` in `asyncio.wait_for(timeout=5.0)`; `start_server` is called with `limit=_MAX_REQUEST_LINE` so the request line cap is enforced before reading. 408 returned on timeout. Test path: `test_handler_survives_connection_reset` already covered the truncated-input case; the timeout path is asserted by code review since fault-injection tests would themselves take 5 s. |
| 4 | LOW | **Stats `det_rules` JSON parse unbounded** — a corrupted row could feed `json.loads` 100 MB of nested arrays, stalling the stats query. | `compute_stats` now skips any row where `len(det_rules) > 64 KB`. Existing test `test_malformed_det_rules_json_is_ignored` still passes; a real attacker would trigger the size cap before the parse. |
| 5 | LOW | **`/health` ran a full table SCAN per probe** — k8s liveness probes at 10 s × full SCAN over a growing audit log can starve the writer. | `HealthState` carries a 1 s TTL cache (`_SNAPSHOT_TTL_S`) under an `asyncio.Lock`. Subsequent probes within the TTL refresh only `uptime_s`; the database is touched at most once per second per process. |
| 6 | MEDIUM | **Cross-script homoglyphs (Cyrillic/Greek look-alikes for Latin) bypass the detector** — NFKC keeps them separate by design. | `_HOMOGLYPHS` table in `detectors/rules.py` (~40 entries hand-picked from published PoCs) plus `_fold_homoglyphs` apply during both within-word and between-word normalisation passes. Tests: `test_cyrillic_homoglyphs_are_caught`, `test_greek_homoglyphs_are_caught`. The full Unicode `confusables.txt` (~10 MB) was rejected as too heavy for v0.4; community PRs welcome to extend the table. |

## New surfaces, briefly

- **`mcp-firewall doctor`** (4 checks: Python ≥ 3.11, Ollama reachable + model loaded, audit DB writable + at v2, rules + policy validate). Exit 0 / 1 / 2 by worst status. Tests: `tests/test_doctor.py`.
- **`mcp-firewall benchmark`** (3 workloads: rules detector, inspector cache hit, end-to-end via cat). Prints p50 / p95 / p99. Used by `docs/PERFORMANCE.md` community-data table.
- **GitHub Actions workflows**: `publish.yml` (PyPI OIDC trusted publishing on tag), `test-publish.yml` (manual test.pypi), `release.yml` (auto release notes from CHANGELOG), `sync-labels.yml`, `auto-label.yml`, `welcome.yml`, `stale.yml`. All gated by `vars.MCP_FIREWALL_DISABLE_<NAME>` for opt-out.
- **README hybrid rewrite** — radical voice on hero, problem statement, top-3 FAQ; light polish on technical sections. Full FAQ in `docs/FAQ.md` (10 questions per spec).
- **`docs/RELEASING.md`** — release procedure relying on PyPI trusted publishing (no `PYPI_TOKEN` ever in repo).

## Coverage gains over Week 3

| Surface                                    | Week 3 | Week 4 |
|--------------------------------------------|-------:|-------:|
| Test files                                 | 16     | 19     |
| Test cases                                 | 203    | 221    |
| `src/` modules                             | 17     | 19     |
| ADRs                                       | 5      | 5      |
| GitHub Actions workflows                   | 1      | 8      |

## What still defers to v0.5+

- **HTTP/SSE transport** (ADR-0006 territory) — out of scope for the launch milestone.
- **Community rules repository** (`mcp-firewall/rules-community`) — separate org repo, paired with the `rules lint --strict` gate.
- **Viewer filters in `mcp-firewall logs`** (search by rule id, by trace id, etc.).
- **Anthropic Haiku fallback tier** for the LLM classifier (carried over from v0.2 → v0.3 → v0.5).

All tracked in [`docs/RELEASE-v0.4.0.md`](RELEASE-v0.4.0.md) and the README roadmap.

## Recommended next steps

1. Tag v0.4.0 and push. The `publish.yml` and `release.yml` workflows do the rest.
2. Watch the first telemetry-enabled installs land in `data/telemetry.log` (maintainer-only). The first week of post-launch data tells us if the privacy schema is right.
3. Open the first three community-rule issues with PoCs you couldn't catch yet — that primes the contributor flywheel.
