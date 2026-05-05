# mcp-firewall v0.3.0 — Community readiness, observability, audit-fix harvest

**Date:** 2026-05-05
**Status:** alpha — opt-in detector and telemetry; off-by-default posture preserved.

## Headline

`mcp-firewall` is now ready for the public OSS launch. Three things changed:

1. **Audit-fix harvest from v0.2.** The five deferred findings from the Week-2 self-audit are landed: NFKC + invisible-char three-pass scan, per-member inspection of JSON-RPC batch frames, explicit `skipped:non_text_content` audit note, one-end truncation closing the seam evasion, and the `Haiku fallback` deferred to v0.4 with an explicit ADR slot.
2. **Observability** without compromising the privacy posture: a local `stats` command, a loopback `/health` endpoint, and an opt-in telemetry pipe that ships nothing more than version + OS + event counts.
3. **Community readiness** — `CONTRIBUTING.md` (with the rule-pack promotion ladder), `SECURITY.md` (GitHub Security Advisories), three integration-tested MCP servers (`github`, `brave-search`, `postgres`).

```bash
# Try the new commands:
mcp-firewall stats --since 24h
mcp-firewall stats --json --compact
mcp-firewall rules lint --strict src/mcp_firewall/rules/builtin/
mcp-firewall run --server "..." --health-port 8765
```

## What's new

### Stats (local-only)

`mcp-firewall stats` is a read-only roll-up of the audit log. Rich-table by default, JSON via `--json` (pretty by default; `--compact` for one-line). Window selector `--since 7d|24h|30m`. Versioned schema (`schema_version: 1`) so future changes are non-breaking.

### Health endpoint

`--health-port N` binds a tiny asyncio listener on `127.0.0.1:N` and serves one route: `GET /health → 200 application/json`. For k8s liveness probes and `docker HEALTHCHECK`. Loopback-only by design — no auth, no TLS, no external exposure.

### Telemetry — opt-in, anonymous, transparent

- **Off by default.** Only the env var `MCP_FIREWALL_TELEMETRY=true` enables it.
- **Privacy contract.** No rule names, no method names, no traffic content, no IPs/hostnames. Only `installation_id` (a self-mintable UUID), version, OS family, Python version, and four integer event counts.
- **Local log first.** Every payload is appended to `data/telemetry.log` *before* the HTTP call. Network errors never erase the log entry.
- **Endpoint kill-switch.** `MCP_FIREWALL_TELEMETRY_URL=disabled` skips the HTTP call but still writes the local log.
- **Files mode 0600.** Co-tenants on a shared machine can't read payloads.
- Full schema and "what we DON'T send" in [`docs/OBSERVABILITY.md`](OBSERVABILITY.md).

### Rules lint

`mcp-firewall rules lint <path>` validates community-contributed YAML packs:

- Basic mode: syntax, regex compilation, valid `apply_to`, score in `[0.0, 1.0]`. Equivalent to load-time validation.
- `--strict`: + `description ≥ 10` chars + `source` is an HTTP(S) URL + `severity_tier` set + `attack_examples` list with at least one entry that *actually matches* the regex + `false_positive_examples` (when present) must NOT match.

The strict bar is the gate for promotion from `community/` to `built-in/` (see [`CONTRIBUTING.md`](../CONTRIBUTING.md)).

### Audit-fix harvest

| Finding (Week 2)                          | v0.3 fix                                                       |
|-------------------------------------------|----------------------------------------------------------------|
| NFKC normalisation                        | Three-pass scan: raw + within-word + between-word normalised   |
| Batch frame per-member inspection         | Per-member rows in audit log; whole-batch block on any hit     |
| Non-text content shapes                   | `note=skipped:non_text_content` in audit                       |
| Truncation seam                           | One-end (head-only) truncation; `note=ok:truncated=<chars>`    |
| Haiku fallback                            | Deferred to v0.4 (async-parallel inspection redesign)          |

### Privacy hardening (v0.3 audit findings)

The Week-3 self-audit surfaced 4 high/medium issues already closed in this release:

- `platform.release()` reduced to its first numeric component (was leaking custom kernel build strings).
- `data/telemetry.log` and `data/installation_id` written with mode `0600`.
- `_trace_id` no longer mixes the raw frame into the SHA1 seed (`os.urandom(8)` is sufficient and faster).
- LLM truncation is now visible in audit via `note=ok:truncated=<chars>`.

Full report: [`docs/AUDIT-REPORT-week3.md`](AUDIT-REPORT-week3.md).

## Tested MCP integrations

| Server | Threat the integration test asserts the proxy catches |
|---|---|
| `github-mcp-server` | Role-hijack + exfiltration text inside an issue body |
| `brave-search-mcp` | Search-snippet poisoning (page text steers the agent) |
| `postgres-mcp` | Stored injection in a TEXT column the agent reads |

Adding your favourite server: see [`docs/INTEGRATIONS.md`](INTEGRATIONS.md) and the per-server template in `tests/integration/`.

## Test coverage

`pytest tests/` jumps from **121 cases (v0.2)** to **203 cases (v0.3)**, all green. New surfaces:

- `tests/test_stats.py` (21 cases) — windowing, percentiles, JSON shape.
- `tests/test_telemetry.py` (25 cases) — env handling, identity stability, payload privacy contract, transmission paths.
- `tests/test_health.py` (7 cases) — loopback-only enforcement, hostile-peer survival.
- `tests/test_lint.py` (14 cases) — basic + strict mode, drifted attack-examples, malicious YAML.
- `tests/integration/{github,brave_search,postgres}/*.py` (9 cases) — smoke + benign + attack per server.

## Compatibility

- Python ≥ 3.11.
- AGPL-3.0-or-later.
- **No new runtime dependencies.** All four new modules (`stats.py`, `telemetry.py`, `health.py`, `lint.py`) reuse existing deps.
- Detector is **off by default**. Telemetry is **off by default and opt-in only**. Health endpoint is **off by default**. Existing v0.2 users keep their behaviour.

## Migrating from v0.2.0

1. `pip install -U mcp-firewall` (or `pip install -e .` from the repo).
2. Existing `log.db` opens unchanged — schema version stays at 2.
3. To get the new audit-fix protections, no config change is needed; they are always-on improvements to the detection layer.
4. To enable observability:
   ```bash
   mcp-firewall stats                          # local read of the audit log
   mcp-firewall run ... --health-port 8765     # k8s/docker probe
   export MCP_FIREWALL_TELEMETRY=true          # opt-in telemetry, off by default
   ```

## Known limitations (v0.4 backlog)

- ReDoS guard for community-contributed regex (no pattern-time budget yet).
- Per-id error synthesis on JSON-RPC batch block (the wire currently desyncs from the audit log when only some members block).
- Health endpoint per-connection timeout + read-byte cap.
- Cross-script homoglyph coverage (Cyrillic look-alikes for Latin letters) — needs a `confusables` table (~10 MB).
- Anthropic Haiku fallback tier (carried over from v0.2).

Tracking is in `docs/AUDIT-REPORT-week3.md` §"Deferred to v0.4".

## Acknowledgements

The Week-3 adversarial review (10 findings, 4 closed here, 6 documented for v0.4) was performed by a sub-agent run against the codebase. Same threat model as ADR-0004 / ADR-0005.

Rule-pack signatures continue to be sourced from public corpora — see [`docs/THREATS.md`](THREATS.md) for the per-rule provenance table.
