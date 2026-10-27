# LogStore Demo Design

## Intent

Complete the existing RocksDB log store as a dependable, easy-to-demonstrate local application for Topic 21. The current project already has a useful RocksDB core, CLI, Flask page, benchmark, and 19 store tests. The demo still lacks user administration, record editing/deletion, import/export, and an application workflow for backup/restore. The rubric also expects a GUI demonstration of database operations.

## Goals

- Provide first-run administrator setup, login/logout, and admin/operator/viewer roles.
- Let authorized users add, query, edit, and delete events. Keep event IDs stable when editing and update the primary record and all indexes atomically.
- Support time, level, source, message, and `trace_id` filtering, with `trace_id` indexed in RocksDB.
- Import and export UTF-8 JSONL with validation and bounded input sizes.
- Create RocksDB checkpoints and restore a selected checkpoint into a new directory before switching the running app to it.
- Keep the application local by default, provide a repeatable demo flow, and organize tests and supporting documents by purpose.
- Match the rubric's 50–60 page report requirement in the preparation notes; the Word report and presentation are deferred until the demo is complete, as requested.

## Non-goals

- Production deployment, remote exposure, multi-host replication, or a general-purpose log analytics platform.
- Preparing the final Word report or presentation in this implementation phase.
- Replacing RocksDB or adding a SQL database.

## Approaches Considered

1. **Keep the demo unauthenticated and add only the missing data buttons.** This is the smallest implementation, but does not meet the user-administration rubric item and leaves destructive actions unprotected.
2. **Add a small local account workflow and complete the database operations in the existing Flask app.** First-run setup creates one administrator; the administrator manages roles. Existing Flask, Werkzeug, and RocksDB are sufficient. This is the selected approach because it directly covers the assessed demo functions without introducing a new service or dependency.
3. **Rebuild the project as a larger client/server application with a separate identity store and background job system.** This adds operational complexity that the local coursework demo does not need.

## Design Decisions

- Keep `app.py`, `cli.py`, and `bench.py` as simple root entry points. Keep `logstore/` as the storage core and `templates/` as the Flask views. Group tests and supporting documents into named subfolders rather than changing the Python import model.
- Store account records in a RocksDB `users` column family. Store only Werkzeug password hashes; never store plaintext passwords.
- Allow only the first administrator to be created through the one-time setup page. The administrator can add users with `admin`, `operator`, or `viewer` roles. Operators can manage events and backups; viewers can query; only administrators can manage users and restore checkpoints.
- The event's 12-byte key remains unchanged when its fields are edited. Primary value and secondary indexes are changed in one `WriteBatch`.
- JSONL import validates the entire bounded file before writing. Export writes canonical UTF-8 JSONL records with event timestamps and fields; imported records receive new RocksDB event IDs.
- Restore copies a chosen checkpoint to a new unique directory, opens and validates that DB, then swaps the app's active store. It never copies over an open RocksDB directory.
- Reject invalid or dangerous inputs at API boundaries, including negative query limits and non-positive retention windows.
- Keep all destructive UI operations behind an explicit confirmation and role check.
- Provide an admin-triggered, idempotent seed with synthetic staff profiles and trace-correlated business workflows. Never overwrite the first admin or existing accounts; use `.test` email addresses and a temporary password entered by the admin.

## Constraints and Assumptions

- Preserve the original ZIP archive. Work only in the extracted review copy at `E:\DoAn_NoSQL\_review\rocksdb_logstore`.
- Continue using Flask and `rocksdict`; avoid adding runtime dependencies unless the existing stack cannot support a requirement.
- Run the app on `127.0.0.1` by default. A stable session key can be supplied with `LOGSTORE_SECRET_KEY`; otherwise a per-process secret logs users out after restart.
- The user has asked to complete and verify the demo first. Report and slide authoring remain a later phase.
- The official rubric is authoritative where the existing report outline conflicts with it.

## Acceptance Criteria

- Existing storage behaviors remain covered and the complete automated suite passes.
- Regression tests prove that `limit=0` returns no rows and negative purge hours are rejected without deleting data.
- Tests cover indexed trace lookup, event update/delete index consistency, first-admin setup, role enforcement, account management, JSONL round-trip, checkpoint creation, and app restore.
- The browser demo visibly exposes the supported user and database operations and reports errors clearly.
- README instructions let a teammate create a virtual environment, install dependencies, initialize the demo, run it, and follow a reproducible demonstration sequence.
- The realistic demo seed creates four fictional operator/viewer profiles and 32 linked events across five traces without duplicating data on repeat runs.
- The extracted project has a clear folder map and the original ZIP remains untouched.
