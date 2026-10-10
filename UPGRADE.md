# Upgrading Journeyman

This document describes how to upgrade an **existing Journeyman
installation based on the latest published GitHub release** to a newer
Journeyman release or development build.

It assumes Journeyman was originally installed using the supplied Ansible
deployment playbook and that the existing inventory and host variables
are still available.

The upgrade must preserve the PostgreSQL database, `/etc/journeyman`
configuration, credential encryption keys, runner PKI, existing
application data, Projects, Packages, Inventories, Credentials, Schedules
and Jobs.

Do **not** treat an upgrade as a fresh installation.

## 1. Before upgrading

Review the release notes, `CHANGELOG.md`, and any version-specific upgrade
notes. If upgrading across more than one released version, review every
intervening release.

Retain the existing Ansible inventory and host variables. In particular,
retain database, proxy, outbound allow-list, LDAP/AD, runner, TLS/PKI and
notification settings.

Record the currently installed database revision and services:

Journeyman 2.0 uses **one** application environment path, regardless of
Python version: `/opt/journeyman/venv`. The installer builds it if missing.
Do not rename an existing Python venv to this path (virtual environments can
contain absolute shebangs); retain `venv314` as a rollback copy until validated.

```bash
systemctl --no-pager --type=service | grep -i journeyman
cd /opt/journeyman
/opt/journeyman/venv/bin/flask --app run.py db current
```

If only the legacy venv exists, inspect the current database revision with
that environment before running the new installer. Remove any old
`journeyman_venv` host-var override.

## 2. Back up the installation

Take a PostgreSQL backup before applying new code or migrations.

Example:

```bash
pg_dump \
  --format=custom \
  --file=/var/tmp/journeyman-pre-upgrade.pgc \
  journeyman
```

For a remote database, add the appropriate `-h`, `-p` and `-U` options.

Verify the backup:

```bash
ls -lh /var/tmp/journeyman-pre-upgrade.pgc
```

Back up Journeyman configuration and cryptographic material:

```bash
tar -C /etc \
  -czf /var/tmp/journeyman-etc-pre-upgrade.tar.gz \
  journeyman
```

The contents of `/etc/journeyman` may include material that must **not** be regenerated during a normal upgrade, including credential encryption keys and runner PKI.

Also review locally used state under:

```text
/etc/journeyman
/var/lib/journeyman
/opt/journeyman
```

## 3. Obtain the new source

Download the required release from:

```text
https://github.com/coec/journeyman/releases
```

The latest published GitHub release is:

```text
https://github.com/coec/journeyman/releases/latest
```

For a development upgrade, use the supplied archive or an explicitly selected Git commit.

Extract the new source into a staging directory first:

```bash
mkdir -p /var/tmp/journeyman-upgrade
cd /var/tmp/journeyman-upgrade

unzip /path/to/journeyman-<version>.zip
```

or:

```bash
tar -xf /path/to/journeyman-<version>.tar.gz
```

Do not remove the existing `/opt/journeyman` tree until the replacement source has been checked.

## 4. Stop Journeyman services

Stop components that can create or modify Jobs during the upgrade.

Typical units are:

```bash
systemctl stop journeyman-scheduler
systemctl stop journeyman-runner
systemctl stop journeyman-web
```

Confirm the locally installed unit names:

```bash
systemctl list-unit-files | grep -i journeyman
```

Remote runners do not normally need to be stopped. They should tolerate temporary loss of the controller and resume communication when it returns.

## 5. Install the new application code

The preferred upgrade method is to rerun the supplied Journeyman deployment playbook using the **existing inventory and host variables**.

The installer now always uses `/opt/journeyman/venv`. Remove previous
noncanonical `journeyman_venv` settings from inventory/host vars.

The deployment must be treated as an in-place upgrade. It must not remove or regenerate:

```text
/etc/journeyman
/etc/journeyman/credential-keys
runner CA/private keys
the PostgreSQL database
persistent application state under /var/lib/journeyman
```

If replacing `/opt/journeyman` manually, stage the old application tree rather than deleting it:

```bash
mv /opt/journeyman /opt/journeyman.previous
mv /var/tmp/journeyman-upgrade/<new-tree> /opt/journeyman
```

Restore only installation-specific files that are deliberately stored inside the application tree.

Do **not** copy the old Python virtual environment over the new release. Prefer allowing the deployment playbook to recreate or update it.

Configure `journeyman_venv` to the desired path and allow the deployment
playbook to create or update that environment.

## 6. Update Python dependencies

If the deployment playbook does not do this automatically:

```bash
cd /opt/journeyman
source "${JOURNEYMAN_VENV}/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use the dependency mechanism supplied by the release. Do not arbitrarily upgrade packages beyond the versions supported by Journeyman.

## 7. Apply database migrations

Apply migrations after installing the new code and before restarting normal services:

```bash
cd /opt/journeyman
source venv/bin/activate

flask --app run.py db current
flask --app run.py db upgrade
flask --app run.py db current
```

The final command should show the migration revision shipped with the new release.

Do not run migrations simultaneously from multiple Journeyman nodes.

## 8. Check ownership and permissions

Journeyman services normally run as the `journeyman` account.

After any manual upgrade, check that persistent state has not become owned by `root`:

```bash
namei -l /var/lib/journeyman

find /var/lib/journeyman -maxdepth 2 \
  \( ! -user journeyman -o ! -group journeyman \) \
  -ls
```

Avoid running operational commands as `root` when they create persistent Journeyman data.

For example, a manual scheduler run should use the service account:

```bash
sudo -u journeyman "${JOURNEYMAN_VENV}/bin/flask" \
  --app /opt/journeyman/run.py \
  run-scheduler --once
```

## 9. Start Journeyman

If systemd units changed:

```bash
systemctl daemon-reload
```

Start the installed Journeyman services:

```bash
systemctl start journeyman-web
systemctl start journeyman-runner
systemctl start journeyman-scheduler
```

Use only the units present on the system.

## 10. Verify the upgrade

Check service state:

```bash
systemctl --no-pager --full status journeyman-web
systemctl --no-pager --full status journeyman-scheduler
systemctl --no-pager --full status journeyman-runner
```

Check recent logs:

```bash
journalctl \
  -u journeyman-web \
  -u journeyman-scheduler \
  -u journeyman-runner \
  --since "-10 minutes" \
  --no-pager
```

Verify the database revision:

```bash
cd /opt/journeyman
source "${JOURNEYMAN_VENV}/bin/activate"
flask --app run.py db current
```

Verify that the installed services and helper scripts use the expected
application environment:

```bash
systemctl cat journeyman-web
systemctl cat journeyman-scheduler

head -1 /opt/journeyman/bin/journeyman-*

test -x "${JOURNEYMAN_VENV}/bin/python3"
test -x "${JOURNEYMAN_VENV}/bin/gunicorn"
test -x "${JOURNEYMAN_VENV}/bin/flask"
```

Log in and confirm that existing Projects, Packages, Inventories, Credentials, Environments, Runners, Schedules, Jobs and application settings remain present.

Pay particular attention to AD/LDAP configuration and runner enrolment state.

Run a small Job that uses an existing stored Credential. This confirms that the existing credential encryption keyring survived the upgrade and that previously encrypted data remains decryptable.

Run a small Job through a remote runner and verify that the execution slice and parent Job both reach terminal states.

For installations using schedules, verify that the scheduler processes a due occurrence.

For Projects using **Exclusive** concurrency, an occurrence that becomes due while another Job is active should be recorded as skipped rather than creating another Job.

## 11. Remote runner upgrades

Remote runner software should remain compatible with the controller version.

Where a release changes the remote runner executable, environment, systemd unit or supporting RPMs, use Journeyman's runner deployment/reconciliation mechanism rather than manually replacing individual files where possible.

The remote runner unit should provide its runtime directory using systemd:

```ini
[Service]
RuntimeDirectory=journeyman
RuntimeDirectoryMode=0755
```

Current releases may retain unacknowledged remote completion records under:

```text
/var/lib/journeyman/remote-runner/completions
```

Do not remove this directory as part of routine cleanup or upgrade processing.

A completion record remains until the Journeyman controller acknowledges it. This prevents a temporary controller outage from permanently leaving a remote Job in `running`.

## 12. Behaviour during controller restart

Remote runners are intended to tolerate temporary loss of `journeyman-web`.

During an upgrade:

- an executing remote process may continue while the controller is unavailable;
- heartbeats and management calls may temporarily fail;
- completed remote execution results should be retained until acknowledged;
- queued and scheduled work should resume when the controller returns.

A transient controller outage must not leave a Job permanently blocking an **Exclusive** Project.

A useful post-upgrade test is:

1. Start a non-production remote Job that runs for several minutes.
2. Stop `journeyman-web`.
3. Allow the remote execution to finish.
4. Wait at least one runner heartbeat interval.
5. Start `journeyman-web`.
6. Verify that the Job reaches a terminal state.
7. Verify that subsequent Jobs continue to run.

## 13. Rollback

Application rollback and database rollback are separate operations.

If database migrations were applied, restoring only the previous `/opt/journeyman` tree may be insufficient because the previous application may not understand the newer schema.

The safest rollback is:

1. Stop Journeyman services.
2. Restore the pre-upgrade PostgreSQL backup.
3. Restore the previous application tree.
4. Restore `/etc/journeyman` only if it was changed.
5. Restore deployment-managed service files if required.
6. Run `systemctl daemon-reload`.
7. Start Journeyman.
8. Verify database revision, credentials, runners and Jobs.

For a custom-format `pg_dump`, use `pg_restore` against an appropriately prepared database.

Do not remove the failed upgraded installation until the cause of the failure has been established.

## 14. Upgrade checklist

Before:

```text
[ ] Read release/change notes
[ ] Retain existing Ansible inventory and host_vars
[ ] Record current database migration revision
[ ] Back up PostgreSQL
[ ] Back up /etc/journeyman
[ ] Confirm credential encryption keys are included
[ ] Record active Journeyman systemd units
```

Upgrade:

```text
[ ] Stop scheduler
[ ] Stop built-in runner if present
[ ] Stop web service
[ ] Install new application code
[ ] Update/recreate Python environment
[ ] Confirm journeyman_venv matches the installed application environment
[ ] Apply database migrations
[ ] Check ownership and permissions
[ ] Reload systemd if required
[ ] Start services
```

After:

```text
[ ] Services are healthy
[ ] systemd units and helper scripts use the expected application environment
[ ] Database is at the expected migration revision
[ ] Existing application settings remain present
[ ] Existing Credentials decrypt successfully
[ ] Existing remote runners are Healthy
[ ] Test Job succeeds
[ ] Scheduled Job succeeds
[ ] Controller restart does not permanently strand remote execution
[ ] No unexpected errors appear in the journal
```

## 15. Development builds

A development build may contain database migrations, runner protocol changes or partially completed features that have not yet appeared in a published GitHub release.

Before installing a development build over the latest GitHub release:

- take a fresh PostgreSQL backup;
- retain the exact source archive or Git commit used;
- run the full test suite where practical;
- test the upgrade on a non-production copy first;
- assume database downgrade may require restoring the database backup.

Record the Git commit used for the deployed build as part of the upgrade change record.

## Journeyman 2.0 canonical runner layout

For each remote runner, standard paths are:

- `/opt/journeyman/venv` (runner agent Python; managed execution environments remain separate)
- `/etc/journeyman/remote-runner.env` (registration)
- `journeyman-remote-runner.service` (systemd service)
- `/run/journeyman/ansible-cp` (systemd-managed runtime directory)
- `/etc/journeyman/runner-pki` (existing identity, NEVER delete on update)

Use **Manage Remote Runner → Update** to migrate a previously named-instance
runner. The update verifies the UUID against the registered runner, refuses
multiple registrations on one host, stops its old instance, moves existing
work/environment/spool paths only when there are no conflicts, and backs up
the old environment file with a `.migrated-<timestamp>` suffix. It does not
re-register the runner or rotate the private key unless the normal recovery
mechanism is needed. Do not delete old `venv314` directories until jobs,
heartbeats, certificate renewal and environment sync have been verified.

**Do not run both named and canonical service instances simultaneously.**
If the migration refuses an ambiguous registration or conflicting data path,
resolve it manually before retrying; it will not merge or overwrite data.

On the controller, rerun `install-journeyman.yml` after deploying updated RPM
files so `/opt/journeyman/venv` is populated and systemd units are updated.
Verify `journeyman-runner`, `journeyman-environment-builder`, scheduler and web
before updating remote nodes.
