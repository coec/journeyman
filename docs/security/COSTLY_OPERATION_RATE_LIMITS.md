# Costly Operation Rate Limits

Journeyman applies database-backed per-user and global request budgets to
resource-intensive operations. The backing records are stored in the audit
log, so limits are shared across Gunicorn workers and survive worker restarts.

The protected operation families are:

- Project and Package execution previews, including inventory/repository work.
- Project and Package execution launches.
- Inventory refresh.
- Repository synchronization.
- Managed execution-environment build and rebuild.

An allowed attempt is committed before expensive work begins. Failed backend
operations therefore still consume budget. If rate-limit state cannot be
persisted, Journeyman fails closed with HTTP 503 rather than performing the
expensive operation without a limit.

When either the per-user or global budget is exhausted, the request returns
HTTP 429 with `Retry-After`.

The default window is 300 seconds. Sysadmins can tune the limits using:

- `JOURNEYMAN_COSTLY_OPERATION_WINDOW_SECONDS`
- `JOURNEYMAN_COSTLY_PREVIEW_USER_LIMIT` / `_GLOBAL_LIMIT`
- `JOURNEYMAN_COSTLY_LAUNCH_USER_LIMIT` / `_GLOBAL_LIMIT`
- `JOURNEYMAN_COSTLY_INVENTORY_USER_LIMIT` / `_GLOBAL_LIMIT`
- `JOURNEYMAN_COSTLY_REPOSITORY_USER_LIMIT` / `_GLOBAL_LIMIT`
- `JOURNEYMAN_COSTLY_ENVIRONMENT_USER_LIMIT` / `_GLOBAL_LIMIT`

Defaults intentionally permit normal interactive administration while bounding
burst automation and accidental request loops. Authentication has its own
separate login-attempt limiter.
