# Local Operations Hardening Implementation Plan

> **For agentic workers:** Execute inline in this session. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the existing local RocksDB LogStore safer to operate and easier to verify without changing its single-host architecture.

**Architecture:** Extend the current Flask app and RocksDB store. Persist producer-scoped idempotency keys atomically with events, run opt-in maintenance in the active server process, expose audit records through an admin-only page, throttle login attempts in memory, and measure benchmark behavior on the current machine.

**Tech Stack:** Python 3.12, Flask, Waitress, rocksdict, unittest, standard library.

**Spec:** `docs/superpowers/specs/2026-10-27-local-operations-hardening-design.md`

## Global Constraints

- Keep the application and RocksDB on one host and one active DB process.
- Keep automatic retention and backup pruning disabled unless explicitly configured.
- Do not expose, log, or commit secrets; preserve the legacy single-token ingest path when unambiguous.
- Use temporary DBs for tests and benchmarks; do not change active demo data or backups.
- Add no runtime dependency unless existing facilities cannot meet the requirement.

## Review Focus

- Same producer and source event ID with different payload must fail without partial writes.
- Duplicate IDs inside one ingest batch must not produce duplicate events or inconsistent response IDs.
- Retention must not start in tests/imported app factories, must use the post-restore active store, and must stop/join before RocksDB closes.
- Backup rotation validates RocksDB checkpoints read-only, discovers their existing column families for legacy compatibility, does not follow paths outside the backup root, and does not delete the only valid checkpoint.
- Login throttling must not permanently lock users or grow memory without bound.
- Audit pagination must filter noncanonical seeded `audit-service` events without skipping later canonical audit records.

---

### Task 1: Producer credentials and idempotent ingestion

**Files:**
- Modify: `logstore/store.py`, `webapp/__init__.py`, `webapp/api.py`, `examples/emit_logs.py`, `.env.example`, `README.md`
- Test: `tests/unit/test_store.py`, `tests/integration/test_app_events.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- `LogStore.put_ingested_many(events, producer_id, source_event_ids)` returns event IDs in request order and the count of newly written events; identical replays return existing IDs.
- `LOGSTORE_INGEST_TOKENS` is a JSON mapping from producer ID to token; producer identity is derived from the token and token values must be unique. `LOGSTORE_INGEST_TOKEN` remains a single-producer fallback only when the mapping is absent.
- `/api/ingest` requires `source_event_id` per record and returns `written`, `duplicates`, and IDs in input order.

- [x] Write tests for replay, conflicting payload, duplicates within one batch, producer isolation, invalid token, legacy-token compatibility, mapping cleanup on delete and both retention paths, and persistence after reopen.
- [x] Run targeted tests and verify expected failures.
- [x] Add the idempotency column family and atomic event/index writes; update deletion and retention to remove reverse mappings.
- [x] Implement producer-token configuration and request validation; update the sample emitter and configuration docs.
- [x] Run targeted tests and the full suite.

### Task 2: Opt-in scheduled retention and safe checkpoint rotation

**Files:**
- Create: `webapp/maintenance.py`
- Modify: `webapp/__init__.py`, `app.py`, `webapp/api.py`, `.env.example`, `README.md`
- Test: `tests/unit/test_server.py`, `tests/integration/test_app_runtime.py`, `tests/integration/test_app_events.py`

**Interfaces:**
- `LOGSTORE_RETENTION_HOURS=0` disables scheduled deletion; a positive value enables it.
- `LOGSTORE_RETENTION_INTERVAL_SECONDS` controls the maintenance interval and is bounded.
- `LOGSTORE_BACKUP_RETENTION_COUNT=0` disables pruning; positive values retain that many valid checkpoints.
- The maintenance worker starts only in the executable server path and uses the app's active store/write lock.

- [x] Write tests for disabled retention, configured one-shot retention, audit and idempotency preservation/cleanup, worker shutdown/join, backup rotation and protection of the newest/only valid checkpoint.
- [x] Run targeted tests and verify expected failures.
- [x] Implement a stoppable daemon worker that uses the live store reference, logs run counts/errors, and is joined before store close.
- [x] Implement path-safe backup rotation after successful checkpoint creation.
- [x] Verify restore recovers both event rows and user accounts; run targeted tests and the full suite.

### Task 3: Admin audit viewer and login throttling

**Files:**
- Create: `templates/audit.html`
- Modify: `webapp/auth.py`, `webapp/api.py`, `templates/base.html`, `README.md`
- Test: `tests/integration/test_app_auth.py`, `tests/integration/test_app_events.py`

**Interfaces:**
- `GET /audit` and `GET /api/audit` are admin-only; the API supports bounded cursor pagination.
- Login throttling returns 429 and `Retry-After` during a temporary cooldown, then permits a valid login after expiry or reset.

- [x] Write tests for admin/viewer access, canonical audit filtering across pages, audit fields/secret exclusion, throttling, cooldown expiry and bounded state.
- [x] Run targeted tests and verify expected failures.
- [x] Add the read-only audit page and API using the existing audit event index.
- [x] Add bounded per-account/per-client login throttling without a new dependency.
- [x] Run targeted tests and the full suite.

### Task 4: Benchmark keyword search and publish measured results

**Files:**
- Modify: `bench.py`, `tests/unit/test_bench.py`, `README.md`
- Create: `docs/benchmark-results/2026-10-27-local-operations.md`, `docs/benchmark-results/2026-10-27-local-operations.json`

**Interfaces:**
- Benchmark output includes RocksDB and SQLite substring-search timing and sample count.
- The report records actual OS, CPU, memory, Python, workload and raw measured values.

- [x] Add a failing benchmark helper test for keyword-query samples and summary output.
- [x] Run the helper test and verify the expected failure.
- [x] Add equivalent substring query measurement to both benchmark backends.
- [x] Run a 200,000-event benchmark into the documentation output path and record only observed results.
- [x] Document interpretation and limits without claiming one engine is universally faster.
- [x] Run the benchmark tests and full suite.

### Final verification

- [x] Run `python -m unittest discover -s tests -v` with isolated `LOGSTORE_PATH` and backup paths.
- [x] Run Python compilation for the application, CLI, benchmark and tests.
- [x] Re-read the spec and check every acceptance criterion against test output, benchmark artifacts and docs.
- [x] Inspect `git diff --check`, `git status`, and the complete diff; confirm no demo DB, credentials or benchmark scratch files are included.
