# Production Runtime Design

## Intent

Make the existing single-node RocksDB demo safer to run and easier to verify as a service, while keeping the current Windows local workflow.

## Goals

- Serve requests through Waitress rather than Flask's development server.
- Keep loopback binding as the default and make host, port, and thread count configurable.
- Expose unauthenticated liveness and readiness probes that reveal no database details.
- Require a strong persistent session secret in production and enable secure session cookies there.
- Provide a small Docker Compose deployment with persistent RocksDB data, a health check, and a loopback-only published port.
- Keep all current app behavior and automated tests working.
- Make benchmark percentile sample counts explicit and summarize the small number of independent write trials without labeling their maximum as a p99 estimate.
- Preserve canonical audit events when ordinary retention removes older log rows.

## Non-goals

- Public Internet deployment, TLS termination, reverse-proxy configuration, multi-host replication, or orchestration beyond one Compose service.
- Rate limiting or a multi-tenant API-key manager.
- Changing the event schema, query semantics, or the benchmark workload.

## Approaches

1. Keep the Flask development server and document manual precautions. This avoids dependencies but does not model service operation.
2. Add Waitress, service probes, strict production secret handling, and a small Compose deployment, while correcting audit retention and benchmark reporting. This is selected because it closes operational gaps without changing the storage architecture.
3. Rebuild as a multi-service production stack with proxy, metrics service, and external authentication. This exceeds the single-node coursework goal.

## Design decisions

- Add Waitress as the only runtime dependency needed for the WSGI server.
- Read `LOGSTORE_HOST`, `LOGSTORE_PORT`, and `LOGSTORE_THREADS`; default to `127.0.0.1`, `5000`, and `8`.
- Treat `LOGSTORE_ENV=production` as a strict mode: require a nonblank session key of at least 32 characters and set `SESSION_COOKIE_SECURE` by default. An explicit `LOGSTORE_COOKIE_SECURE=false` is allowed for the loopback-only HTTP Compose demo; HTTPS deployments keep it enabled.
- Use `/health/live` for process liveness and `/health/ready` for a lightweight RocksDB read. Responses contain status only.
- Run the container as a non-root user, store DB/checkpoints in a named volume, and publish its port to host loopback by default. The Compose example explicitly disables Secure cookies because it serves plain HTTP only on host loopback.
- Keep native Windows instructions first-class; Compose is an additional run path.
- Use range tombstones for ordinary retention when no audit rows are in range; use bounded per-event WriteBatches when audit rows need to be preserved.

## Acceptance criteria

- Tests cover server configuration bounds, local defaults, liveness/readiness behavior, and production secret/cookie policy.
- The full `unittest` suite and Python compilation pass.
- Docker Compose configuration validates, the image builds, and a container becomes healthy and serves the setup page while its data volume persists across restart.
- README explains both local and Compose run paths, required secrets, and the local-only network binding.
- Retention tests prove audit rows and their indexes survive while expired ordinary events are removed.
- Benchmark summaries expose sample counts and environment metadata to help reproduce a run.
