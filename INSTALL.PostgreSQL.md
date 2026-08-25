# Journeyman with PostgreSQL

PostgreSQL is the recommended database for operational Journeyman installations. This
document covers both the tested single-host/local-database pattern and the additional
requirements for a remote PostgreSQL service.

The recommended new-install path is `deploy/ansible/install-journeyman.yml`, which
performs the local PostgreSQL setup automatically. The commands below document the
same steps for manual installs and troubleshooting.

## 1. Install and initialise PostgreSQL

On the tested RHEL-family host:

```bash
yum install postgresql-server
mkdir -p /var/lib/pgsql/data
/usr/bin/postgresql-setup --initdb
systemctl enable --now postgresql
```

The systemd service name is normally `postgresql`.

## 2. Use SCRAM password authentication

Set this in the active `postgresql.conf` before creating/resetting the Journeyman
role password:

```text
password_encryption = 'scram-sha-256'
```

Then reload/restart PostgreSQL and verify:

```bash
sudo -u postgres psql -tAc 'SHOW password_encryption;'
```

Expected result:

```text
scram-sha-256
```

Changing `password_encryption` does not convert an existing MD5 verifier. Reset the
Journeyman role password after changing the setting so PostgreSQL writes a SCRAM
verifier.

## 3. Configure local host-based authentication

For PostgreSQL on the Journeyman host, keep TCP access narrow. A typical
`pg_hba.conf` contains peer authentication for local administrative socket access and
specific SCRAM rules for Journeyman:

```text
local   all            all                                  peer
host    journeyman     journeyman    127.0.0.1/32           scram-sha-256
host    journeyman     journeyman    ::1/128                scram-sha-256
```

`pg_hba.conf` is first-match-wins. Put the specific Journeyman rules before any
broader TCP rule that could match the same connection. There is no need to permit the
PostgreSQL administrator over TCP merely to administer a local instance; use the Unix
socket/peer path with `sudo -u postgres psql`.

Reload PostgreSQL after changing HBA rules:

```bash
systemctl reload postgresql
```

## 4. Create the database role and database

Choose a strong, unique password. As the local PostgreSQL administrator:

```bash
sudo -u postgres psql
```

Then:

```sql
CREATE ROLE journeyman
    LOGIN
    PASSWORD 'REPLACE-WITH-A-STRONG-UNIQUE-PASSWORD';

CREATE DATABASE journeyman
    OWNER journeyman
    ENCODING 'UTF8'
    TEMPLATE template0;

REVOKE ALL ON DATABASE journeyman FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE journeyman TO journeyman;

\connect journeyman
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO journeyman;
```

Journeyman does not require PostgreSQL superuser, `CREATEDB`, `CREATEROLE`, or
replication privileges.

If the role existed before SCRAM was enabled, reset its password now:

```sql
ALTER ROLE journeyman WITH PASSWORD 'REPLACE-WITH-A-STRONG-UNIQUE-PASSWORD';
```

## 5. Install the Python PostgreSQL requirements

Run pip as the Journeyman service account:

```bash
sudo -u journeyman -H /bin/bash
cd /opt/journeyman
/opt/journeyman/venv/bin/python -m pip install -r requirements-postgresql.lock
exit
```

This installs the exact base application and Psycopg dependency versions validated
for this Journeyman release. The human-maintained `requirements-postgresql.txt`
records direct dependencies; production installation deliberately uses the lock file
so pip does not re-resolve transitive dependencies at install time.

Verify the resulting environment:

```bash
/opt/journeyman/venv/bin/python scripts/check_dependency_lock.py --postgresql
```

## 6. Configure the database URI

Set `database.uri` in `/etc/journeyman/journeyman.yml`.
For a local server:

```yaml
database:
  uri: postgresql+psycopg://journeyman:DB_PASSWORD@127.0.0.1:5432/journeyman
```

If the password contains URI-reserved characters such as `@`, `:`, `/`, `?`, `#`, or
`%`, percent-encode it before placing it in the URI.

For a remote PostgreSQL server, use a finite connect timeout and verified TLS when
available, for example:

```yaml
database:
  uri: "postgresql+psycopg://journeyman:DB_PASSWORD@postgres.example.com:5432/journeyman?sslmode=verify-full&sslrootcert=/etc/journeyman/postgresql-ca.pem&connect_timeout=10"
```

Restrict network/firewall access to the required Journeyman application hosts. A
remote CA file should be readable by the service account but not writable by it.

## 7. Select the configuration before command-line tests

The installed `/opt/journeyman/.bashrc` activates the application virtual environment;
Journeyman command-line tools load `/etc/journeyman/journeyman.yml` themselves for interactive shells started as the
`journeyman` account with `HOME=/opt/journeyman`. For root shells, installation
bootstrap commands, or environments where that profile has not yet been installed,
load and export the environment explicitly before running Flask/SQLAlchemy tests:

```bash
cd /opt/journeyman
export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
```

Without this step, a command may silently use the development/default SQLite URI
instead of the configured PostgreSQL database.

## 8. Test the connection

Using the loaded production environment:

```bash
sudo -u journeyman -H /bin/bash -c '
  cd /opt/journeyman
  export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
  /opt/journeyman/venv/bin/python - <<"PY"
from sqlalchemy import create_engine, text
from app.config import Config

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
with engine.connect() as connection:
    print(connection.execute(text("SELECT version()")) .scalar())
PY
'
```

A successful command prints the PostgreSQL server version.

Some libpq builds attempt GSS encryption before password authentication. On a host
where Kerberos/GSS is intentionally not used for this connection, `PGGSSENCMODE=disable`
can be set for a diagnostic `psql` test to remove the unrelated GSS negotiation
message.

## 9. Create or upgrade the Journeyman schema

Run committed migrations as the Journeyman account **before starting the scheduler,
runner, or environment builder**:

```bash
sudo -u journeyman -H /bin/bash -c '
  cd /opt/journeyman
  export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
  /opt/journeyman/venv/bin/flask --app run.py db upgrade
'
```

On a new empty database this creates the complete schema. On upgrades it applies only
new migrations. Do not use `db.create_all()` as a substitute for the committed
migration history.

Migration code must use native SQL boolean values (`TRUE`/`FALSE`, or SQLAlchemy
`sa.true()`/`sa.false()`) rather than SQLite-style integer `1`/`0` defaults. The test
suite contains a portability check for this class of regression.

## 10. Verify schema ownership

As a PostgreSQL administrator:

```sql
\connect journeyman
\dt
\dn+ public
```

The Journeyman database role should own/control its application objects without using
a privileged PostgreSQL role.

## Backup and recovery

Use the organisation's normal PostgreSQL backup/recovery tooling and test restoration
periodically. A database backup is not sufficient by itself: separately back up
`/etc/journeyman/credential.key`, because encrypted Journeyman credentials cannot be
recovered without it.

PostgreSQL HA, replication, external pooling, and managed database services are
platform responsibilities outside Journeyman itself.
