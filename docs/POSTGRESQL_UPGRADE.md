# PostgreSQL upgrade process

Journeyman treats PostgreSQL engine upgrades separately from Journeyman schema upgrades. The database engine must be upgraded using the supported Red Hat/PostgreSQL procedure for the installed RHEL release; Journeyman does not run `dnf module reset`, replace PostgreSQL packages, or invoke `pg_upgrade` automatically.

## Before the database upgrade

1. Put Journeyman into a maintenance window and stop all writers: `journeyman`, `journeyman-runner`, `journeyman-scheduler`, and `journeyman-environment-builder`.
2. Run `scripts/journeyman-postgresql-upgrade preflight` and retain the reported PostgreSQL and Alembic revisions with the change record.
3. Create a logical rollback copy with `scripts/journeyman-postgresql-upgrade backup`. The generated custom-format dump is mode `0600` and is validated with `pg_restore --list` before success is reported.
4. Take the normal host/database backup required by the site change process. The Journeyman dump is an additional application-aware rollback artefact, not a replacement for platform backup policy.

## Upgrade PostgreSQL

Perform the PostgreSQL engine upgrade using the procedure supported for the installed RHEL major release and the selected PostgreSQL packaging source. Preserve the `journeyman` database, role, local `pg_hba.conf` restrictions, TLS settings where used, and the configured database encoding/locale.

Do not start Journeyman application services until PostgreSQL itself starts cleanly and the database is available.

## After the database upgrade

1. Run `scripts/journeyman-postgresql-upgrade verify` to prove SQLAlchemy can connect and report the new server version.
2. Upgrade the Journeyman application/schema in the normal release order. When the application files for the new Journeyman release are already installed, `scripts/journeyman-postgresql-upgrade verify --upgrade-schema` may be used to run `flask db upgrade` and then re-check connectivity.
3. Start Journeyman services and run the built-in **Release Testing** suite before returning the instance to service.
4. Retain the pre-upgrade dump until the release/change rollback period has expired.

## Rollback boundary

If the PostgreSQL major-version upgrade fails, restore the previous PostgreSQL engine/data directory using the platform-supported rollback method or create a clean compatible PostgreSQL instance and restore the validated custom-format dump with `pg_restore`. Do not restore a PostgreSQL data directory onto a different major engine version.
