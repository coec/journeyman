# Journeyman with SQLite

SQLite is the simplest database option for Journeyman and requires no separate
database server. It is recommended for development, evaluation, laboratories, and
small non-critical single-server installations.

For production or operationally important deployments, PostgreSQL is preferred.
SQLite permits only limited concurrent write activity; Journeyman can have several
writers (the web application, scheduler, local runner, remote-runner result updates,
and live Job output).

## When SQLite is a reasonable choice

SQLite is a reasonable choice when all of the following are true:

- there is one Journeyman application server;
- database high availability is not required;
- normally no more than one or two Jobs execute concurrently;
- Job frequency and administrative activity are modest;
- short periods of database write contention would not create an operational risk;
- the deployment can be migrated to PostgreSQL later if usage grows.

These are operational guidelines, not hard product limits. Host count alone is not
a good sizing measure: a small inventory can generate heavy write activity when many
Jobs and execution slices run concurrently.

## Configure the database path

Create the data directory and ensure the Journeyman service account owns it:

```bash
sudo install -d -o journeyman -g journeyman -m 0750 /var/lib/journeyman
```

Set the database URI in `/etc/journeyman/journeyman.yml`:

```yaml
database:
  uri: sqlite:////var/lib/journeyman/journeyman.db
```

The four slashes are intentional: the final path is an absolute filesystem path.
Protect the YAML configuration file according to `INSTALL.md`.

## Create or upgrade the schema

Run migrations as the Journeyman service account:

```bash
cd /opt/journeyman
sudo -u journeyman /opt/journeyman/venv314/bin/flask --app run.py db upgrade
```

Then verify the database file exists and is not writable by unrelated users:

```bash
ls -l /var/lib/journeyman/journeyman.db
```

## Backup

Do not copy an actively changing SQLite database with a plain `cp` and assume the
copy is consistent. Use SQLite's backup facilities or stop all Journeyman services
before copying the database file. Always back up the Journeyman credential
encryption key separately as described in `INSTALL.md`.

## Moving to PostgreSQL

Journeyman does not currently provide an automated SQLite-to-PostgreSQL data
migration utility. Plan and test any database-engine migration before a production
cutover. A new deployment can instead be initialized directly on PostgreSQL by
following `INSTALL.PostgreSQL.md`.
