# ADR-0005: Observability layer and opt-in telemetry

- **Status:** proposed
- **Date:** 2026-05-05
- **Deciders:** @churik
- **Extends:** ADR-0003 (event log schema), ADR-0004 (detection layer)

## Context

Week 2 shipped a working detection layer; Week 3 prepares for the public OSS launch. We need three observability primitives without breaking the privacy posture that makes a self-hosted firewall trustworthy:

1. **Local stats** — the user can answer "how many attacks did the firewall catch this week?" with one command, without writing SQL.
2. **Health probes** — k8s / docker / supervisord users need a non-stdio way to assert the proxy is alive.
3. **Anonymous usage telemetry** — the maintainer can answer "should I keep building this?" with concrete activation numbers, without spying on users.

The non-negotiable constraint: a self-hosted firewall **cannot** ship default-on telemetry, and even when opted in it must never reveal the user's traffic, configuration, or environment beyond what is strictly necessary for product-health analytics.

## Decision

### 1. `mcp-firewall stats` — local-only

A read-only CLI command that queries the existing audit DB (`events` table from ADR-0003) and prints aggregate counts.

- **Default output**: Rich-rendered table (verdict counts, top-5 rules, latency p50/p95).
- **Scriptable output**: `--json` (pretty by default) and `--json --compact` (single line).
- **Time window**: `--since 7d | 24h | 1h | …` (default 7d).
- **JSON schema is versioned**: every JSON payload includes `schema_version: 1`. We commit to never breaking format-1 fields silently; new fields can be added.

```json
{
  "schema_version": 1,
  "period_start": "2026-04-28T00:00:00+00:00",
  "period_end":   "2026-05-05T00:00:00+00:00",
  "total_events": 14879,
  "verdicts": {"PASS": 14820, "WARN": 47, "BLOCK": 12},
  "top_rules": [
    {"id": "role_hijack.ignore_previous", "count": 8},
    {"id": "exfil.send_to_url",            "count": 3}
  ],
  "latency_ms": {"p50": 0.04, "p95": 0.13}
}
```

### 2. Health endpoint (`--health-port N`)

When `mcp-firewall run` is launched with `--health-port N`, a minimal HTTP server listens on `127.0.0.1:N` (loopback only — never exposed). One route:

- `GET /health` → `200 application/json` with `{status, uptime_s, events_processed, last_event_ts, version}`.

The server runs as an `asyncio` task next to the pump tasks; failure to bind the port logs a warning but does **not** crash the proxy. Port `0` (the default when the flag is omitted) disables the server entirely. No auth, no TLS — the listener is loopback-only on purpose; if you want it exposed, put it behind a reverse proxy you trust.

### 3. Telemetry — strict opt-in

Telemetry is **disabled by default**. To turn it on, the user sets `MCP_FIREWALL_TELEMETRY=true` in the environment of the `mcp-firewall run` process. The endpoint URL is configurable via `MCP_FIREWALL_TELEMETRY_URL` (default ships as `https://telemetry.example.com/v1/ingest`, which the maintainer replaces before announcing the launch).

#### Payload schema

The minimum we can sustain ourselves from. **No traffic content. No rule names. No method names. No server commands.**

```json
{
  "schema_version": 1,
  "installation_id": "8a1b2c…",
  "version": "0.3.0",
  "platform": "darwin",
  "platform_release": "23.0.0",
  "python_version": "3.12",
  "days_active": 14,
  "events_total": 12450,
  "events_blocked": 8,
  "events_warned": 23,
  "events_passed": 12419,
  "detector_enabled": true
}
```

`installation_id` is a random UUID generated on first telemetry-enabled run and stored in `data/installation_id`. Removing the file resets the identity. We use it only to deduplicate the same installation hitting the endpoint multiple times per day.

#### What we explicitly DO NOT send

- Rule IDs and rule pack names — they would reveal the user's threat model and could fingerprint deployments.
- Method names, JSON-RPC ids, server commands, `--server` strings.
- Audit log contents (raw, params, results, errors).
- IP, hostname, MAC address, container id, hardware UUID — anything tying the installation to a person or company.
- Configuration files, custom rule packs, custom policies, even their existence.

#### Transparency mechanics

- **First-run banner.** The first time the proxy starts with telemetry enabled, it writes a multi-line notice to stderr: what is sent, where, how to disable. We never silence it on subsequent runs — we just rate-limit to once per day per process.
- **Local log.** Every payload is appended verbatim to `data/telemetry.log` (one JSON object per line) **before** the HTTP call. The user can `cat` it any time.
- **Silent fail on network errors.** Telemetry must never block, slow, or crash the proxy. HTTP timeouts default to 5 s; on failure we log to `data/telemetry.log` with a `"status": "error"` field and move on.
- **Endpoint kill-switch.** Setting `MCP_FIREWALL_TELEMETRY_URL=disabled` skips the HTTP call entirely while still writing the local log — useful for offline development and for users who want to inspect what would be sent without sending it.

#### Cadence

- One transmission attempt per process-lifetime, on first idle window after start (proxy waits 60 s after launch so we don't spam on flapping restarts).
- A second transmission for any process that runs longer than 24 h, then every 24 h thereafter.
- Process exit drops any pending transmission — we never queue.

### 4. Configuration / CLI surface

```yaml
# config.example.yaml additions
telemetry:
  enabled: false           # only the env var actually flips it; YAML key is documentation
  endpoint: ""             # empty = use built-in default; "disabled" = local-log-only

observability:
  health_port: 0           # 0 = disabled; >0 = bind 127.0.0.1:<port>/health
```

CLI flags layered on top:

- `mcp-firewall run --health-port 8765` (overrides config)
- `mcp-firewall stats [--since 7d] [--json [--compact]]`

`MCP_FIREWALL_TELEMETRY` and `MCP_FIREWALL_TELEMETRY_URL` are environment-only; we deliberately do not ship CLI flags for them so a habitual flag-flipper cannot enable telemetry across one invocation. Opt-in is a choice, not a side effect.

## Consequences

**Positive**

- Zero-network-by-default. A user who never sets the env var never makes a single HTTP call out of the firewall.
- The audit DB is the single source of truth for stats; we don't grow a parallel metrics store.
- The schema is versioned from day one, so we can extend telemetry in v0.4+ without breaking the maintainer's ingest pipeline.
- The local `telemetry.log` makes the system auditable by the user with stock Unix tools.

**Negative / accepted trade-offs**

- We will never know which rules people use most — `top_rules` was deliberately removed. We accept that as the price of trust.
- The HTTP server is one extra moving part. We mitigate by binding loopback-only and by isolating bind failures from the pump.
- `installation_id` provides weak persistence, easily reset by deleting the file. That's by design — we want users to be able to "forget" themselves.

## Alternatives considered

- **Default-on telemetry with an opt-out** — rejected. Trust in a security tool is asymmetric: one default-on dump is enough to lose every privacy-conscious user forever.
- **OTLP / Prometheus exposition** — overkill for v0.3 and forces a new dep. Stats over JSON works for the OSS audience; a Prometheus exporter can ride on top in v0.4.
- **Send `top_rules` IDs** — rejected per maintainer instruction. Even rule IDs leak the user's threat model.
- **Persistent telemetry queue** — rejected. We never accumulate undelivered telemetry on the user's disk. If the network is down, we lose that day.

## Things deliberately NOT in v0.3

- TLS certificate pinning for the telemetry endpoint (rely on system TLS).
- Telemetry batching across processes (no shared queue).
- Detailed per-rule firing distributions in stats (we keep it to top-5).
- Authentication on `/health` (loopback-only listener).
