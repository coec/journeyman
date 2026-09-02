# Installing Journeyman

Journeyman is designed to run natively on Linux using systemd. PostgreSQL is the
recommended database for operational deployments.

Two installation methods are documented:

1. **Automated installation using Ansible — preferred**
2. **Manual installation**

For a new RHEL-family server, use Option 1 unless you have a specific reason to install
the components manually.

SQLite is also supported for development, evaluation, and small non-critical
deployments, but the automated production installation described below configures
PostgreSQL.

## Option 1 - Automated installation (preferred)

Journeyman includes an Ansible playbook which builds a complete application server:

```text
deploy/ansible/install-journeyman.yml
```

The simplest installation is to run the playbook **locally on the server that will
become the Journeyman server**.

The examples below use:

```text
journeyman.example.com
```

Replace this with the actual FQDN of your server.

### 1. Prepare the server

Start with a supported RHEL-family Linux server with working:

- DNS
- package repositories
- network access required to download Journeyman's Python dependencies
- a TLS certificate for the Journeyman FQDN

The Journeyman installer installs supplied TLS material but does not issue or manage
certificates.

For a production deployment, use your organisation's existing PKI or another properly
managed certificate authority.

If no CA is available for an evaluation, lab, or bootstrap deployment, see
[`docs/local-ca.md`](docs/local-ca.md) for a simple OpenSSL-based local CA procedure.

Install Git and Ansible:

```bash
dnf install -y git ansible-core
```

### 2. Download Journeyman

Clone the repository directly into `/opt/journeyman`:

```bash
git clone https://github.com/coec/journeyman.git /opt/journeyman
cd /opt/journeyman
```

If you are installing a specific Journeyman release rather than the current `main`
branch, check out the appropriate release tag before continuing.

For example:

```bash
git checkout <release-tag>
```

### 3. Prepare the TLS certificate

You need:

- the server private key
- a PEM file containing the server certificate and any required intermediate
  certificates

The root CA certificate is normally installed separately into the trust store of
systems that need to validate Journeyman's HTTPS certificate.

For a local installation, the certificate and key files can be placed anywhere readable
by the account running Ansible. For example:

```text
/root/journeyman-install/journeyman-key.pem
/root/journeyman-install/journeyman-fullchain.pem
```

The installer copies these files into their final protected locations.

If you do not already have access to a CA, see
[`docs/local-ca.md`](docs/local-ca.md).

### 4. Create the Ansible inventory

From `/opt/journeyman`, create:

```bash
mkdir -p inventory/host_vars
```

Create `inventory/hosts.ini`:

```ini
[journeyman_servers]
journeyman.example.com ansible_connection=local
```

`ansible_connection=local` is important when running the installer on the Journeyman
server itself. It prevents Ansible from trying to SSH back into the same machine.

### 5. Configure the installation

Create:

```text
inventory/host_vars/journeyman.example.com.yml
```

with at least:

```yaml
---
journeyman_fqdn: journeyman.example.com
journeyman_database_password: CHANGE_ME

journeyman_tls_fullchain_src: /root/journeyman-install/journeyman-fullchain.pem
journeyman_tls_key_src: /root/journeyman-install/journeyman-key.pem
```

These four variables are required.

Use a strong database password. For a maintained installation, store the password in
Ansible Vault or another appropriate secret store rather than committing it to Git.

An example variable file is supplied with Journeyman:

```text
deploy/ansible/install-journeyman.example.yml
```

That file also documents optional initial directory-group defaults and the production
outbound allowlist.

The break-glass administrator activation defaults to 60 minutes. For a lab or
evaluation system without directory services, the installer can use a longer lifetime:

```yaml
journeyman_fallback_admin_lifetime_minutes: 10080  # 7 days
```

Setting the value to `0` disables automatic activation expiry. This is strongly
discouraged for production deployments. Normal browser-session lifetime limits still
apply, and explicitly signing out expires the current break-glass activation.
Changing this setting affects newly generated activations; it does not alter an
already active break-glass session.

### 6. Configure a proxy if required

If `pip` must use an HTTP/HTTPS proxy to download Python dependencies, add:

```yaml
journeyman_pip_proxy: http://proxy.example.com:8080
```

If the proxy URL contains credentials, protect the value using Ansible Vault or another
secret-management mechanism.

This variable controls the proxy used by the Python dependency installation. Normal
operating-system repository access must already be configured on the server.

### 7. Run the installer

From `/opt/journeyman`:

```bash
ansible-playbook \
  -i inventory/hosts.ini \
  deploy/ansible/install-journeyman.yml
```

The playbook will:

- install Journeyman's operating-system prerequisites
- create the `journeyman` service account
- create the required runtime directories
- create `/opt/journeyman/venv`
- install the locked Python dependencies
- initialise and configure PostgreSQL
- configure SCRAM database authentication
- create the Journeyman database and database account
- generate the Journeyman credential-encryption key
- apply database migrations
- install the systemd services
- install the Nginx configuration and apply helper
- install the supplied TLS certificate and private key
- configure SELinux for the Nginx reverse proxy where required
- open HTTP and HTTPS in firewalld when firewalld is active
- start Journeyman
- create the initial break-glass administrator account
- perform basic application and database checks

The playbook is intended to be safe to run again. Persistent secrets and the fallback
administrator are not regenerated simply because the installer is rerun.

### 8. Sign in

At the end of a successful installation, the playbook displays the initial break-glass
administrator credentials.

Open:

```text
https://journeyman.example.com/
```

and sign in using that account.

For an operational deployment, next configure:

**Settings -> Directory and Authentication**

Configure and test the required directory servers and verify directory searches before
depending on directory authentication.

### Installing from a separate Ansible control host

Running the installer locally is the simplest method, but a separate Ansible control
host can also be used.

In that case, remove `ansible_connection=local`:

```ini
[journeyman_servers]
journeyman.example.com
```

Configure the normal Ansible SSH and privilege-escalation settings required by your
environment.

For example:

```bash
ansible-playbook \
  -i inventory/hosts.ini \
  -u bootstrap-admin \
  --become \
  deploy/ansible/install-journeyman.yml
```

The Journeyman source tree must already exist at `/opt/journeyman` on the target
server.

Also note that:

```yaml
journeyman_tls_fullchain_src:
journeyman_tls_key_src:
```

refer to files on the **Ansible control host**, because Ansible copies those files to
the target during installation.

## Option 2 - Manual installation

### 1. Operating-system prerequisites

On the RHEL-family build used during installation testing, the required base packages
were installed with:

```bash
yum install postgresql-server nginx python3.14 python3-pip git
```

Package names may differ by RHEL release/repository. Ansible is required only on the
control host when using the installer playbook; managed execution environments can
provide Ansible for automation workloads.

If package or Python dependency access requires an HTTP/HTTPS proxy, configure it in
the normal host environment before running `yum` or `pip`.

### 2. Service account and directories

Create the service account and all runtime directories **before starting any
Journeyman service**:

```bash
useradd -r -d /opt/journeyman -s /sbin/nologin journeyman
install -d -o journeyman -g journeyman -m 0755 /opt/journeyman

mkdir -p \
  /etc/journeyman/tls \
  /var/lib/journeyman/repos \
  /var/lib/journeyman/jobs \
  /var/lib/journeyman/runner-artifacts \
  /var/log/journeyman \
  /var/spool/journeyman/signals \
  /opt/journeyman/environments
chown journeyman:journeyman \
  /var/lib/journeyman/repos \
  /var/lib/journeyman/jobs \
  /var/lib/journeyman/runner-artifacts \
  /var/log/journeyman \
  /var/spool/journeyman \
  /var/spool/journeyman/signals \
  /opt/journeyman/environments

chown root:root /etc/journeyman/tls
chmod 0750 /etc/journeyman/tls
```

Journeyman validates important runtime directories at startup and fails closed when
they are missing or not writable by the service account.

Stage the Journeyman source tree beneath `/opt/journeyman` and make the application
files readable by the service account. For a simple dedicated host, ownership by
`journeyman:journeyman` is appropriate.

### 3. Application virtual environment

The canonical application environment is `/opt/journeyman/venv`. Main-server scripts
and systemd units use this stable path so a Python minor-version change does not
require changing service paths.

Run the Python/pip commands as the `journeyman` account:

```bash
sudo -u journeyman -H /bin/bash
cd /opt/journeyman
python3.14 -m venv /opt/journeyman/venv
/opt/journeyman/venv/bin/python -m pip install --upgrade pip
/opt/journeyman/venv/bin/python -m pip install -r requirements-postgresql.lock
exit
```

Install the administrative shell profile for the dedicated `journeyman` account:

```bash
install -o journeyman -g journeyman -m 0640 \
  /opt/journeyman/deploy/journeyman.bashrc \
  /opt/journeyman/.bashrc
```

Interactive shells started as `journeyman` with its home set to `/opt/journeyman` will
then export `/etc/journeyman/journeyman.yml` and activate `/opt/journeyman/venv`
automatically. The account remains a non-login service account; use an administrative
command such as `sudo -u journeyman -H /bin/bash` when an interactive shell is needed.

Standalone Journeyman utilities also load the environment themselves and do not rely on
this shell profile.

`requirements.txt` and `requirements-postgresql.txt` document Journeyman's direct
Python dependencies. Production installs use `requirements-postgresql.lock`, which
pins the complete direct and transitive dependency graph for the Journeyman release.
This prevents the same Journeyman release resolving to different dependency versions
when installed at different times. Flask-Migrate is a base requirement because
`flask db upgrade` is part of every install/upgrade.

After installation, verify the active environment against the release lock:

```bash
/opt/journeyman/venv/bin/python scripts/check_dependency_lock.py --postgresql
```

### 4. Production YAML configuration file

`/etc/journeyman` is an administrator-controlled configuration directory. The
Journeyman service account needs to read the production YAML configuration file but should
not be able to modify it. Install the example as `root:journeyman` with mode `0640`:

```bash
install -d -o root -g journeyman -m 0750 /etc/journeyman
install -o root -g journeyman -m 0640 \
  /opt/journeyman/deploy/journeyman.yml.example \
  /etc/journeyman/journeyman.yml
```

Generate a persistent unique application secret before production startup:

```bash
/opt/journeyman/venv/bin/python \
  -c 'import secrets; print(secrets.token_hex(32))'
```

Then edit `/etc/journeyman/journeyman.yml` **as root** and place the generated value
there. Set `database.uri` and the initial directory role-group defaults as
needed at the same time, for example:

```yaml
authentication:
  fallback_admin_lifetime_minutes: 60
  directory_admin_group_name: Journeyman Admins
  directory_user_group_name: Journeyman Users
```

`fallback_admin_lifetime_minutes` defaults to `60`. Longer values are supported for
lab/evaluation environments. Setting it to `0` disables automatic activation expiry
and is strongly discouraged for production use. Browser sessions retain their normal
absolute lifetime, and signing out still expires the activation immediately.

Do not change the YAML configuration file ownership to make it writable by the service
account. Application and worker processes should only read this file.

Those directory-group values are used when Directory and Authentication is first
created and can later be changed in the UI. This allows a site to bootstrap
administrators using its existing AD group names.

### 5. Credential-encryption key

The key is required before saving any encrypted setting, including the LDAP bind
password. `/etc/journeyman` is not writable by the service account, so create the key
as root and then give ownership of the key file itself to Journeyman:

```bash
/opt/journeyman/venv/bin/python - <<'PY'
from cryptography.fernet import Fernet
from pathlib import Path

path = Path('/etc/journeyman/credential.key')
path.write_bytes(Fernet.generate_key() + b'\n')
PY
chown journeyman:journeyman /etc/journeyman/credential.key
chmod 0600 /etc/journeyman/credential.key
```

Only the key file is owned by `journeyman`; the `/etc/journeyman` directory and
`journeyman.yml` remain administrator-controlled. Journeyman refuses a credential key
with broader permissions. Back it up separately from the database; encrypted
credentials cannot be recovered without it.

### 6. PostgreSQL

For the tested local PostgreSQL setup, initialise and start the server with:

```bash
mkdir -p /var/lib/pgsql/data
/usr/bin/postgresql-setup --initdb
systemctl enable --now postgresql
```

Set PostgreSQL to create SCRAM verifiers **before setting the Journeyman role
password**:

```text
password_encryption = 'scram-sha-256'
```

Reload/restart PostgreSQL and verify:

```bash
sudo -u postgres psql -tAc 'SHOW password_encryption;'
```

The result should be `scram-sha-256`.

For a database local to the Journeyman host, use narrow `pg_hba.conf` rules such as:

```text
local   all            all                                  peer
host    journeyman     journeyman    127.0.0.1/32           scram-sha-256
host    journeyman     journeyman    ::1/128                scram-sha-256
```

Place the specific Journeyman host rules before any broader TCP rule that would match
the same connection. Do not add broad `0.0.0.0/0` access for a local database.

Create/reset the role password after SCRAM is enabled, create the database, and set
`database.uri` in `/etc/journeyman/journeyman.yml`. Detailed SQL and remote
PostgreSQL guidance are in [`INSTALL.PostgreSQL.md`](INSTALL.PostgreSQL.md).

Before running Flask commands interactively, load the YAML configuration file:

```bash
export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
```

Then apply all committed migrations **before starting the worker services**:

```bash
cd /opt/journeyman
sudo -u journeyman -H /bin/bash -c '
  export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
  /opt/journeyman/venv/bin/flask --app run.py db upgrade
'
```

### 7. TLS and Nginx bootstrap

Production Journeyman uses Secure session cookies, so the first usable login must be
over HTTPS. A plain-HTTP bootstrap will render pages but cannot preserve the session
cookie needed for CSRF validation.

If no suitable CA is available for an evaluation, lab, or bootstrap deployment, see
[`docs/local-ca.md`](docs/local-ca.md).

Create `/etc/journeyman/tls` as root-owned and install the full certificate chain and
private key, for example:

```bash
install -d -o root -g root -m 0750 /etc/journeyman/tls
install -o root -g root -m 0644 ${JOURNEYMAN_TLS_CERTIFICATE_PATH} \
  /etc/journeyman/tls/journeyman-fullchain.pem
install -o root -g root -m 0600 ${JOURNEYMAN_TLS_PRIVATE_KEY_PATH} \
  /etc/journeyman/tls/journeyman-key.pem
```

Set the initial paths/FQDN in `journeyman.yml`:

```yaml
web:
  public_fqdn: journeyman.example.com
  tls_certificate_path: /etc/journeyman/tls/journeyman-fullchain.pem
  tls_private_key_path: /etc/journeyman/tls/journeyman-key.pem
  tls_chain_path: ""
```

Install the privileged Nginx helper and its narrowly-scoped sudo rule:

```bash
install -o root -g root -m 0755 \
  /opt/journeyman/scripts/journeyman-apply-web-settings \
  /usr/local/sbin/journeyman-apply-web-settings
cat >/etc/sudoers.d/journeyman-nginx <<'EOF_SUDO'
journeyman ALL=(root) NOPASSWD: /usr/local/sbin/journeyman-apply-web-settings
EOF_SUDO
chmod 0440 /etc/sudoers.d/journeyman-nginx
visudo -cf /etc/sudoers.d/journeyman-nginx
```

The helper reads a JSON request on stdin when Journeyman invokes it. Running it
interactively with no stdin will appear to wait indefinitely and is not a useful
installation test.

Create the bootstrap Nginx configuration explicitly. Replace
`journeyman.example.com` with the real FQDN if different. Use
`/etc/nginx/conf.d/journeyman.conf` deliberately: after first login, **Settings -> Web
and TLS -> Apply to Nginx** replaces this same file with Journeyman's managed
configuration.

```bash
cat >/etc/nginx/conf.d/journeyman.conf <<'EOF_NGINX'
# Bootstrap configuration. Journeyman will replace this file when Web and TLS
# settings are applied from the UI.
server {
    listen 80;
    server_name journeyman.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name journeyman.example.com;

    ssl_certificate /etc/journeyman/tls/journeyman-fullchain.pem;
    ssl_certificate_key /etc/journeyman/tls/journeyman-key.pem;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
EOF_NGINX

nginx -t
systemctl enable --now nginx
```

On hosts using firewalld, permit HTTP and HTTPS before trying the browser:

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
```

On SELinux-enforcing RHEL hosts, allow Nginx to proxy to the local Gunicorn TCP
listener when required:

```bash
setsebool -P httpd_can_network_connect 1
```

### 8. systemd services

Install the reference units:

```bash
install -o root -g root -m 0644 deploy/systemd/journeyman.service \
  /etc/systemd/system/journeyman.service
install -o root -g root -m 0644 deploy/systemd/journeyman-web.service \
  /etc/systemd/system/journeyman-web.service
install -o root -g root -m 0644 deploy/systemd/journeyman-runner.service \
  /etc/systemd/system/journeyman-runner.service
install -o root -g root -m 0644 deploy/systemd/journeyman-scheduler.service \
  /etc/systemd/system/journeyman-scheduler.service
install -o root -g root -m 0644 deploy/systemd/journeyman-environment-builder.service \
  /etc/systemd/system/journeyman-environment-builder.service
systemctl daemon-reload
systemctl disable journeyman-web journeyman-runner journeyman-scheduler journeyman-environment-builder || true
systemctl enable --now journeyman
```

### 9. Verify the web stack

Verify both layers after the Journeyman services are running:

```bash
curl -I http://127.0.0.1:5000/login
curl -kI https://journeyman.example.com/login
```

The first command checks Gunicorn directly; the second checks the complete HTTPS
bootstrap path through Nginx. A 502 from Nginx with a failed local curl is a
Journeyman/Gunicorn startup problem, not an Nginx routing problem.

### 10. Break-glass administrator and directory bootstrap

A new database has no directory configuration, so AD authentication cannot be the
first login. Generate the local fallback administrator **as root**. The command writes
a root-controlled hash beneath `/etc/journeyman` and is intentionally not run as the
`journeyman` service account:

```bash
cd /opt/journeyman
export JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml
/opt/journeyman/venv/bin/flask --app run.py fallback-admin generate
```

The configured lifetime can be overridden for one activation:

```bash
/opt/journeyman/venv/bin/flask --app run.py fallback-admin generate \
  --lifetime-minutes 10080
```

For a deliberately non-expiring lab/evaluation activation:

```bash
/opt/journeyman/venv/bin/flask --app run.py fallback-admin generate --no-expiry
```

`--no-expiry` is strongly discouraged for production deployments. It disables only
the automatic activation deadline: normal browser-session lifetime limits remain in
force, and explicitly signing out expires the activation immediately.

The password is shown once; Journeyman stores only its salted hash. Sign in with this
account, configure **Settings -> Directory and Authentication**, and validate both
LDAPS servers, the Base DN, user/group search bases, and both role groups. Once AD
login is verified, retain the fallback account only as the documented break-glass
path.

## SQLite

For SQLite development/evaluation installs, see [`INSTALL.SQLite.md`](INSTALL.SQLite.md).

## Upgrade procedure

1. Back up the database and `/etc/journeyman/credential.key` separately.
2. Stop the complete main-server application with `systemctl stop journeyman`.
3. Update the source tree and install the current `journeyman.service` plus all child unit files, including `journeyman-web.service`.
4. Install updated Python requirements into `/opt/journeyman/venv`.
5. Load `/etc/journeyman/journeyman.yml` and run `flask db upgrade`.
6. Run `systemctl daemon-reload`, ensure only `journeyman.service` is enabled, then start it. The coordinator disables accidental child-unit enablement and prepares the managed session-signing key before the children start.
7. Run operational checks/tests.

## Remote runners

Remote runners have their own bootstrap playbook:

```text
deploy/ansible/install-remote-runner.yml
```

Create the Runner in the Journeyman **Runners** page first and use its one-time
registration token with that playbook. Remote-runner installation is independent of
the main-server installer described above.

### YAML configuration upgrade compatibility

Current service units set `JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml`.

For upgrades from the earlier environment-file configuration, Journeyman also
recognises the legacy `JOURNEYMAN_CONFIG=app.config.ProductionConfig` selector
and translates it in-process to the YAML configuration path while preserving the
Flask configuration class. Installed systemd units should still be replaced with
the current packaged units at the next deployment.

