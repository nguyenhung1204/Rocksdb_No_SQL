# LogStore Demo Implementation Plan

> **For agentic workers:** Execute inline in this session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Complete for the demo phase. Word report and presentation remain deferred per the user's request.

**Goal:** Complete and verify the Topic 21 RocksDB demo with user administration, event management, indexed queries, JSONL exchange, backup/restore, and clear project organization.

**Architecture:** Keep the existing Flask entry point and `logstore/` module. Extend RocksDB with trace and user column families, expose role-protected app routes, and build the UI on the existing templates. Move tests and supporting notes into purpose-named folders without changing how users start the app.

**Tech Stack:** Python 3.12, Flask, Werkzeug (already included with Flask), rocksdict/RocksDB, unittest.

**Spec:** `docs/superpowers/specs/2026-10-27-logstore-demo-design.md`

## Global Constraints

- Preserve the original ZIP archive and edit only the extracted review copy.
- Do not add runtime dependencies unless required by the existing stack.
- Restore checkpoints into a new directory and switch stores only after the new RocksDB instance opens successfully.
- Hash account passwords and enforce roles on all data-changing operations.
- Keep the demo bound to `127.0.0.1` by default.
- Defer final Word and slide authoring until the demo phase is complete.

## Review Focus

- A zero or negative limit must not accidentally return one event; negative limits must be rejected at the API boundary.
- A negative or zero retention duration must not erase the database through the demo UI/API.
- Event update/delete must leave primary data and every secondary index consistent, including trace index.
- First-run setup must close permanently after an administrator exists; viewers must not reach write or user-management operations.
- A failed JSONL import or checkpoint restore must leave the active database unchanged.

---

### Task 1: RocksDB records, indexes, and account storage

**Files:**
- Modify: `logstore/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Produces `query(..., trace_id=None)`, `update_event(event_id, *, level=None, source=None, msg=None, fields=None)`, `delete_event(event_id)`, and user-record operations `get_user`, `list_users`, `put_user`, and `delete_user`.
- Event edits preserve their ID and atomically update the default, level, source, and trace indexes.

- [x] Add tests that fail for zero-limit queries, trace lookups, edits that change indexed values, deletes, and persisted user records.
- [x] Run those tests and confirm each fails because the behavior is missing or incorrect.
- [x] Add trace and users column families, record CRUD, validation, and index maintenance.
- [x] Run the targeted tests, then the existing store suite.
- [x] Refactor only while the store tests remain green.

### Task 2: Authenticated Flask application and user administration

**Files:**
- Modify: `app.py`
- Create: `templates/login.html`, `templates/setup.html`, `templates/users.html`
- Test: `tests/integration/test_app_auth.py`

**Interfaces:**
- `create_app(db_path=None, backup_path=None, secret_key=None)` returns an isolated Flask app for both the demo and tests.
- The first setup form creates the initial administrator. `GET /api/session` returns the signed-in user's role and CSRF token.
- Admin routes manage users; role checks protect queries, event mutations, retention, imports, backups, and restore.

- [x] Add failing tests for setup-once behavior, password login, logout, user creation/deletion, CSRF validation, and viewer/operator/admin access.
- [x] Run them and confirm the expected authorization failures.
- [x] Implement the app factory, password hashing, session/CSRF handling, account routes, and JSON error responses.
- [x] Run auth integration tests and the complete current suite.

### Task 3: Event CRUD, query API, exchange, and recovery endpoints

**Files:**
- Modify: `app.py`, `logstore/store.py`
- Test: `tests/integration/test_app_events.py`, `tests/integration/test_app_recovery.py`

**Interfaces:**
- `GET /api/events` supports `start`, `end`, `last_minutes`, `level`, `source`, `trace_id`, `q`, and `limit`.
- `POST /api/events`, `PUT /api/events/<id>`, and `DELETE /api/events/<id>` implement event CRUD.
- `GET /api/export` returns JSONL; `POST /api/import` accepts a bounded multipart JSONL file.
- `GET /api/backups`, `POST /api/backup`, and `POST /api/restore` manage named checkpoints and restore to a new active directory.

- [x] Add failing integration tests for query validation, create/update/delete, purge bounds, JSONL round-trip and all-or-nothing validation, checkpoint listing, backup, and restore switching.
- [x] Run them to confirm missing routes or incorrect behavior.
- [x] Implement the API handlers with role/CSRF checks and safe restore path handling.
- [x] Run targeted integration tests, then the complete suite.

### Task 4: Usable demo interface and command-line workflows

**Files:**
- Modify: `templates/index.html`, `cli.py`, `README.md`
- Create: `templates/base.html`, `scripts/` only if a dedicated helper is needed
- Test: `tests/integration/test_cli.py`, relevant UI/API tests

**Interfaces:**
- The home page exposes stats, sample generation, event query/edit/delete, retention, import/export, and backup/restore.
- The user page exposes account administration to admins.
- CLI supports import, export, and restore in addition to existing generation/query/backup operations.

- [x] Add failing CLI/API tests for import/export/restore commands and UI-served workflows.
- [x] Implement accessible forms, confirmation prompts, error messages, and the documented command paths.
- [x] Reorganize tests and preparation documents by purpose; correct the report page-count note to the official 50–60 pages.
- [x] Run the complete automated suite against isolated test databases and perform HTTP smoke checks against the working demo database.
- [x] Verify the folder map, startup instructions, and rubric coverage checklist in README.

### Task 5: Realistic user profiles and trace-linked sample workflows

- [x] Add fictional profiles, roles, email domains reserved for examples, and editable profile fields to user management.
- [x] Generate 32 realistic application/audit events across five trace IDs; keep seeding idempotent and preserve the original admin.
- [x] Add web and CLI seed entry points, document the temporary shared demo password, and verify operator/viewer access.
- [x] Seed the working demo database with 4 sample users and 32 events; verify 5,032 total events and trace queries.
