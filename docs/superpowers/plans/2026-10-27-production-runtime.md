# Production Runtime Implementation Plan

> **For agentic workers:** Execute inline in this session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the LogStore demo through a safer WSGI service with health probes and a reproducible single-container deployment.

**Architecture:** Keep the Flask app factory and RocksDB store. Add Waitress at the existing `app.py` entry point, expose minimal probes from the app factory, validate production session configuration before opening RocksDB, and package the same app in Docker Compose with persistent storage.

**Tech Stack:** Python 3.12, Flask, Waitress, rocksdict, unittest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-27-production-runtime-design.md`

## Global Constraints

- Preserve the Windows local workflow and bind to `127.0.0.1` by default.
- Do not expose secret values or filesystem paths in health responses.
- Keep the event schema and API contract unchanged; ordinary retention preserves canonical audit rows.
- Run tests against temporary databases; use a uniquely named Compose project and remove only its test container/volume.

## Review Focus

- Invalid host, port, or thread settings must fail before the server starts.
- Production startup without a strong persistent secret must fail before opening a database.
- Readiness must return 503 for a failed RocksDB read while liveness still returns 200.
- Container data must survive a container restart.
- Compose must publish only to loopback by default.

---

### Task 1: Service runtime and health probes

**Files:**
- Modify: `requirements.txt`, `app.py`, `webapp/__init__.py`, `logstore/store.py`
- Test: `tests/unit/test_server.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- `server_options(environ=None)` returns Waitress `host`, `port`, and `threads`, validating host nonempty, port 1–65535, threads 1–64.
- `LogStore.ping()` performs a cheap metadata read and returns `True` when the DB is open.
- `GET /health/live` returns `{"status":"live"}`; `GET /health/ready` returns `{"status":"ready"}` or a detail-free 503 response.

- [x] Write tests for local server defaults and invalid configuration; run and observe expected failures.
- [x] Write tests for liveness/readiness and strict production secret/cookie behavior; run and observe expected failures.
- [x] Add the minimal Waitress launcher, health probes, `LogStore.ping()`, and production configuration checks.
- [x] Run targeted tests, then the full suite and Python compilation.

### Task 2: Container packaging and run guide

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`
- Modify: `README.md`

**Interfaces:**
- `docker compose up --build` serves on `http://127.0.0.1:${LOGSTORE_PUBLISHED_PORT:-5000}`.
- Compose runs the app as non-root, mounts a named data volume, and uses `/health/ready` as its health check.
- `.env.example` has empty secrets so production mode cannot accidentally use a committed default key.

- [x] Add the container image, Compose file, ignore rules, and secret-generation instructions.
- [x] Validate Compose config and build the image.
- [x] Start a uniquely named smoke-test project on an available port; check HTTP readiness and persistence through restart; remove only that project and volume.
- [x] Run the full suite and inspect the final diff.
- [x] Commit the verified change.

### Task 3: Benchmark result confidence

**Files:**
- Modify: `bench.py`, `README.md`
- Test: `tests/unit/test_bench.py`

**Interfaces:**
- Query latency summaries include their observation count and nearest-rank p50/p95/p99.
- Independent write trials report min/median/max plus sample count.
- The JSON result records machine, Python, workload, and durability settings.

- [x] Add failing helper tests for sample-aware query and write summaries.
- [x] Implement sample counts, min/median/max independent write summaries, and reproducibility metadata.
- [x] Raise the default query repeat count to 1,000 and update the documented benchmark invocation.
- [x] Run helper tests and a 200,000-event benchmark, then rerun the full suite.

### Task 4: Preserve audit records through retention

**Files:**
- Modify: `logstore/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- `purge_older_than(cutoff_ms, compact=False)` deletes expired ordinary events and returns their count while retaining canonical audit rows and all their indexes.
- The range-tombstone fast path remains when no audits are in range; mixed ranges use bounded atomic batches.

- [x] Write a failing regression for an expired audit row and verify the current purge deletes it.
- [x] Preserve audits with bounded per-event batches and keep total/level counters in the same WriteBatch as each chunk.
- [x] Test index integrity and a batch-boundary case, then rerun retention tests.
- [x] Rerun the full suite and final diff checks.
