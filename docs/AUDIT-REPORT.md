# Week-1 Audit Report

**Date:** 2026-05-04
**Reviewer:** self-audit via Claude Code skills (`skill-security-auditor`, `dependency-auditor`, `pr-review-expert`, `changelog-generator`)
**Scope:** the entire `0.1.0` codebase — 6 source files, 3 test files, 1 CI workflow, 3 ADRs.

## TL;DR

| Check                                | Verdict | Findings                                                                         |
|--------------------------------------|---------|----------------------------------------------------------------------------------|
| `bandit -r src/`                     | ✅ PASS | 0 issues across 1 112 LoC.                                                       |
| `pip-audit` on resolved tree         | ✅ PASS | "No known vulnerabilities found".                                                |
| Security grep (secrets, eval, …)     | ✅ PASS | 0 matches for hardcoded secrets, eval, exec, shell=True, unsafe yaml.load.       |
| `skill-security-auditor`             | ⚠️ WARN | 1 HIGH: "SKILL.md not found" — false positive, this is a project, not a skill.   |
| `commit_linter --strict`             | ✅ PASS | 8 / 8 commits valid Conventional Commits.                                        |
| `pr-review-expert` checklist         | ✅ PASS | All applicable items satisfied (see below).                                      |

**Net verdict: ✅ ship.** No critical or high findings against the project's own code.

---

## 1. Static security analysis (`bandit`)

```
$ bandit -r src/
Total lines of code: 1112
Total issues (by severity):
    High: 0   Medium: 0   Low: 0
```

Bandit's default ruleset covers the OWASP top picks for Python: shell injection, weak crypto, hardcoded passwords, unsafe deserialisation, request-without-timeout, etc. Zero findings.

## 2. Dependency vulnerability scan (`pip-audit`)

```
$ pip-audit
No known vulnerabilities found
```

Resolved tree as of 2026-05-04: pydantic 2.13.3, click 8.3.3, rich 13.9.4, aiosqlite 0.22.1, PyYAML 6.0.3, httpx 0.28.1. Same scan now runs in CI on every push (`audit-deps` job in `.github/workflows/ci.yml`).

## 3. Hand-rolled security greps (from `pr-review-expert`)

| Pattern                                            | Result   |
|----------------------------------------------------|----------|
| Hardcoded `password=`, `secret=`, `api_key=`, `token=`, `private_key=` (long strings) | 0 matches |
| AWS access-key prefix `AKIA…`                      | 0 matches |
| `eval(`, `exec(`, `os.system(`, `os.popen(`        | 0 matches |
| `subprocess.call(..., shell=True)` / any `shell=True` | 0 matches |
| `pickle.loads`, `yaml.load(` (unsafe), `marshal.loads` | 0 matches; only `yaml.safe_load` is used in `config.py` |
| SQL string interpolation: `execute(f"…")`, `.format(SELECT…)` | 0 matches; all SQL is parameter-bound via `?` placeholders |

The `subprocess` call in `proxy.py` uses `asyncio.create_subprocess_exec(*argv, …)` with the argv list produced by `shlex.split(server_command)` — no shell, no string interpolation into the command line.

## 4. `skill-security-auditor`

Run on an isolated copy of `src/` + `tests/` (so the scanner doesn't trip over our `.venv`):

```
SKILL SECURITY AUDIT REPORT — Verdict: ⚠️ WARN
🔴 CRITICAL: 0   🟡 HIGH: 1   ⚪ INFO: 0

🟡 HIGH [STRUCTURE] SKILL.md
   Pattern: SKILL.md not found
   Risk: Missing SKILL.md — not a valid skill directory
```

**Disposition:** false positive. `skill-security-auditor` is built for AI-agent skills. It expects a `SKILL.md` at the root and flags anything else as "not a valid skill directory". `mcp-firewall` is a Python project, not a skill, so the structural finding is not applicable. All *content* checks (eval, network exfiltration, prompt injection in markdown, file-system abuse) returned zero issues.

## 5. Conventional-commit lint

```
$ git log --pretty=format:'%s' main | python3 commit_linter.py --strict
total: 8   valid: 8   invalid: 0
```

CI gains the same check in milestone 2 once we open the repo to outside contributors.

## 6. PR-review-expert checklist (self-applied)

### Scope & Context
- [x] Each commit's subject accurately describes the change.
- [x] Bodies explain *why*, not just *what* (see e.g. `feat(proxy)`'s rationale for argv-form subprocess and `feat(cli)`'s explanation of the shutdown bug fix).
- [N/A] No linked ticket — solo project, ADRs serve as the durable design record.
- [x] No scope creep — each commit is one logical unit.
- [N/A] Breaking changes — initial release.

### Blast radius
- [N/A] No external consumers yet.
- [x] No new env vars without docs (`MCP_FIREWALL_DB`, `MCP_FIREWALL_CONFIG` are documented in `README.md` and `config.example.yaml`).
- [N/A] DB migrations — initial schema, `schema_version` table seeded for future migrations (ADR-0003).

### Security
- [x] No hardcoded secrets (grep clean).
- [x] All SQL is parameter-bound (audited line-by-line in `storage.py`).
- [x] User inputs: only `--server` is non-trivial; passed through `shlex.split` and `subprocess_exec` (argv form).
- [N/A] No auth surface in v0.
- [N/A] No XSS surface in v0.
- [x] Dependencies clean per `pip-audit`.
- [x] Audit log records JSON-RPC frame contents only — exactly the data the firewall is meant to monitor; not "incidental PII" leakage.

### Testing
- [x] Public API has unit tests (parser, EventRecord shaping, Storage round-trip).
- [x] Edge cases: invalid JSON, blank lines, batches, unknown id types, queue overflow, schema CHECK constraint.
- [x] Error paths tested (`parse_error` kind, dropped-events counter).
- [x] End-to-end test spawns the real CLI as a subprocess.
- [x] Test names describe behaviour (`test_drops_when_queue_full`, `test_invalid_json_is_logged_as_parse_error_not_dropped`).

### Performance
- [x] No N+1: bulk inserts via `executemany` from a single batch.
- [x] No unbounded queues: `EventBuffer` has `queue_max=10_000` (configurable) with drop-on-overflow, never blocks the data path.
- [x] All `await`s reviewed; no missing awaits found.
- [x] No heavyweight new dependencies — pydantic, click, rich, aiosqlite, pyyaml, httpx are all stable, single-purpose libraries.

### Code quality
- [x] Lint clean (`ruff check .` and `ruff format --check .`).
- [x] Types clean (`mypy --strict src/ tests/`).
- [x] Comments explain *why* (line-overflow forwarding, queue-vs-block trade-off, `aclose` reasoning) not *what*.
- [x] No `TODO`s left in the diff (only "milestone 2" pointers in ADRs and the example config).

---

## Items deliberately deferred to milestone 2

These are *not* findings — they are scope boundaries chosen up front:

- **No log rotation / vacuum command.** Personal-workstation traffic is ~few MB / month; the runbook documents a manual SQL recipe.
- **No detector / policy engine.** That is the entire point of Week 2. The schema reserves an `events.note` column for detector verdicts so the integration is additive.
- **No HTTP/SSE transport.** stdio covers ~all desktop MCP clients today; HTTP lands when there's a real user asking for it.
- **No multi-process safety on a single DB.** One proxy per server, by design (ADR-0002).

## Sign-off

Audited and ready for the open-source launch following milestone 2.
