# Local Operations Hardening Design

## Intent

Improve the existing RocksDB LogStore so its local demo behaves more like an operated log service: ingestion tolerates retries, retention and backups have safe policies, administrators can inspect audit records, login attempts are throttled, and performance claims come from measurements on the team's machine.

## Scope and load

Keep the current Python, Flask, Waitress, rocksdict and single-host RocksDB architecture. Assume one application process serving a student project and a local data set of hundreds of thousands of events. This work does not add multi-node operation, a message broker, a remote database, or a public Internet deployment.

## Current state

- `/api/ingest` accepts one global Bearer token and assigns a new RocksDB event ID on every successful request.
- Retention is an explicit API/UI/CLI operation; the store already preserves canonical audit events.
- Backup directories are configurable, but the UI creates checkpoints without a rotation policy.
- Successful mutations create immutable audit events in the same RocksDB database; there is no dedicated audit page.
- The login route checks credentials but does not throttle repeated failures.
- The benchmark records workload/environment information and latency summaries, but the current run must be measured on this machine. Keyword search currently scans event messages.

## Goals

1. Give each ingest producer an environment-configured credential and require a stable per-event source ID. Resolve producer identity from the matched credential, reject duplicate token values in configuration, and do not trust a producer ID supplied in the request. Replays with the same producer, source ID, and payload return the original event ID without another write; reusing the ID for different content is a conflict. Preserve the existing single-token configuration as a documented one-producer compatibility path.
2. Add opt-in scheduled retention in the application process. The default performs no automatic deletion. The worker uses the active store and the existing application write lock, and reports each run without exposing secrets.
3. Add configurable checkpoint rotation after a successful backup. Rotation is opt-in, only removes valid named checkpoints inside the configured backup directory, and never removes the newest or only valid checkpoint. Keep restore-to-new-directory behavior.
4. Add an admin-only, read-only audit page and paginated API that expose actor, action, target, result status and timestamp, but no credentials. Be explicit that the audit trail remains in the same database and can be rewound by restoring an older checkpoint.
5. Add bounded login throttling by account and client address. Repeated failures receive a temporary cooldown, successful login clears the account's failure state, and expired or excess in-memory entries are bounded. Do not lock accounts permanently.
6. Measure the existing benchmark on the current machine, include keyword-scan latency, and save only measured results with machine, Python, workload and trial details. Add an inverted index only if the measured results justify its ongoing write/storage cost.

## Non-goals

- Distributed or multi-process RocksDB access, replication, queues, external backup services, external audit services, or a new database.
- Enabling automatic deletion or backup pruning by default.
- Claiming that a same-database audit trail is tamper-proof.
- Changing ordinary event CRUD semantics or adding a new runtime dependency without a demonstrated need.

## Design decisions

- Store ingest idempotency mappings in a new RocksDB column family and update them in the same `WriteBatch` as the event. Use a producer-scoped digest for lookup and a reverse event-ID key so explicit deletion and both retention paths remove the mapping atomically. Verify the mapping survives reopen/restore. A deleted or expired event's idempotency key may be reused; document this bounded guarantee.
- Read producer credentials from a JSON environment mapping. Keep the legacy single-token setting only when no mapping is configured; reject ambiguous configuration. Compare credentials in constant time and never include them in response bodies or logs. Rotation/revocation takes effect after restarting with the updated environment.
- Run scheduled retention only from the executable server entry point, not when tests import the Flask app factory. A daemon worker checks configuration, obtains the application's write lock, resolves the current active store after restore, and logs a result. Shutdown signals and joins the worker before closing RocksDB. A zero retention window disables it.
- Make checkpoint pruning disabled unless a positive keep-count is explicitly configured. Validate path containment and checkpoint shape before deletion; treat pruning errors as warnings after the new backup has been created.
- Reuse the existing `audit-service` source index for the admin-only audit API, but return only canonical audit events (`is_internal_audit_event`); seeded `audit-service` examples are ordinary events and must not appear as audit records. Pagination must advance through ordinary rows without skipping canonical audit rows. Add only the display/API surface; do not create an unsupported claim of independent or tamper-proof storage.
- Implement login throttling with bounded standard-library state and a configurable or documented fixed policy; return HTTP 429 with `Retry-After` while the cooldown is active.
- Extend `bench.py` with a comparable RocksDB/SQLite substring query measurement. Record output under versioned docs, not the active demo data directory.

## Acceptance criteria

- Ingest tests prove stable IDs and no extra event on replay, reject conflicting replay payloads, enforce producer credentials, and retain legacy single-token compatibility.
- Retention tests prove the default is disabled, the worker stops and joins before store close, configured runs use the current store, idempotency mappings are cleaned on both purge paths, and audits survive.
- Backup tests prove rotation respects its configured count and protects the newest/only valid checkpoint; restore tests verify both events and user accounts.
- Audit tests prove admin-only access, canonical-record filtering across pages, required fields, and absence of password hashes/tokens.
- Login tests prove throttling, cooldown expiry/reset behavior and bounded state without permanent account lockout.
- Benchmark includes message substring scans; a real 200,000-event run is recorded with actual hardware/runtime metadata and no fabricated conclusions.
- Full unit/integration suite and Python compilation pass using isolated temporary databases. Existing demo data and backups are not reset or overwritten.
