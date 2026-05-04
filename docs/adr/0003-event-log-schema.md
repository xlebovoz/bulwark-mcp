# ADR-0003: Event log schema

- **Status:** accepted
- **Date:** 2026-05-04
- **Deciders:** @churik

## Context

ADR-0002 picks SQLite. This ADR fixes the table layout for v0. Goals: cheap append, fast `tail` and `filter by method`, room to add detector verdicts later without a migration.

## Schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version(version) VALUES (1);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,        -- ISO-8601 UTC
    ended_at        TEXT,                 -- NULL while running
    server_command  TEXT NOT NULL,
    client_pid      INTEGER,
    server_pid      INTEGER,
    exit_code       INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts           TEXT    NOT NULL,        -- ISO-8601 UTC, with microseconds
    direction    TEXT    NOT NULL CHECK (direction IN ('client_to_server','server_to_client')),
    kind         TEXT    NOT NULL CHECK (kind IN ('request','response','notification','error','raw','parse_error')),
    msg_id       TEXT,                    -- JSON-RPC id (string for portability), nullable for notifications
    method       TEXT,                    -- only for requests/notifications
    params_json  TEXT,                    -- raw JSON string, nullable
    result_json  TEXT,                    -- raw JSON string, nullable
    error_json   TEXT,                    -- raw JSON string, nullable
    raw          TEXT    NOT NULL,        -- the original line as received (for forensics)
    note         TEXT                     -- reserved for future detector verdicts
);

CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_method      ON events(method) WHERE method IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_kind        ON events(kind);
```

## Rationale

- **`id` as autoincrement integer** — natural ordering for `tail`. No need for UUIDs in a single-machine local DB.
- **Timestamps as ISO-8601 text** — SQLite has no native timestamp type; ISO text sorts lexicographically, is human-readable, and survives DB dumps.
- **`raw` always populated** — even on `parse_error` we keep the original bytes, so a reviewer can reconstruct what the client/server actually sent. Crucial for prompt-injection forensics: a malformed frame is a signal, not noise.
- **JSON columns as TEXT, not native JSON1** — broader SQLite compatibility (Python ships with JSON1 since 3.38, but some distros lag). Indexing on `method` is enough for v0; we do not need JSON-path indexes yet.
- **`schema_version` table** — primes us for migrations. Bump the integer when shape changes; the `Storage.init_db` method will branch on it.
- **`note` column reserved** — we will populate it from the detector in milestone 2 (`"injection-suspect"`, `"blocked"`, `"warned-and-confirmed"`). Adding a column later is an `ALTER TABLE`; reserving it now keeps milestone-2 changes additive.

## Things deliberately NOT in v0

- No soft deletes (logs are append-only).
- No multi-tenancy / RLS (single-user desktop tool).
- No partitioning by date (premature; rotate via separate command later).
- No FTS index on `raw` (separately enabled in milestone 2 if detector needs full-text search).
