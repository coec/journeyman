# Protected Data Retention

Journeyman retains Job execution evidence and Reaction history because they are
operationally useful, but protected data must not be retained indefinitely merely
because storage is available.

The default policy retains completed Jobs and terminal Reactions for **180 days**.
Administrators can change the two periods independently under **System Settings →
Data Retention**. A value of `0` retains that record type indefinitely and should
only be used where an explicit deployment retention policy requires it.

Job retention applies only to terminal Jobs (`successful`, `failed`, or
`cancelled`) and therefore never removes queued or running execution state.
Purging a Job removes its dependent steps, stdout/stderr, host results, execution
slices, credential snapshots, inventory snapshots, repository snapshots, Package
snapshot, stored inventory files, remote repository artifacts, and local Job
workspace.

Reaction retention applies only to terminal Reaction history (`observed`,
`successful`, `failed`, `cancelled`, or `suppressed`). Pending, queued, running,
or cancelling Reactions are never removed by retention.

`JOURNEYMAN_JOB_RETENTION_DAYS` and `JOURNEYMAN_REACTION_RETENTION_DAYS` remain
startup/fallback defaults for deployments that do not yet have the singleton
System Settings row. Once System Settings exists, the database values configured
through the UI are authoritative.

Resolved source-inventory cache files can contain sensitive host variables. They
are therefore shorter lived: the default maximum cache age is **7 days**
(`JOURNEYMAN_INVENTORY_CACHE_RETENTION_SECONDS=604800`). Expired cache data is
removed from the filesystem and is recreated by the normal inventory refresh
path when required.

The scheduler evaluates the retention policy at least hourly by default. The
interval is configurable with
`JOURNEYMAN_DATA_RETENTION_PURGE_INTERVAL_SECONDS`. Administrators can inspect or
run the same policy explicitly with:

    flask purge-retained-data --dry-run
    flask purge-retained-data

Application-level deletion does not guarantee forensic overwrite of database
pages, WAL/journals, filesystem snapshots, storage-array snapshots, or backups.
Those copies must be protected and expired by the deployment's database,
filesystem, and backup retention policies. Journeyman's responsibility is to
stop retaining the protected application records once their configured period
has expired.

All authenticated application responses are emitted with `Cache-Control:
no-store` plus compatible no-cache headers so protected HTML/JSON does not
remain in browser or intermediary shared caches after use.
