# Configuration Security

Journeyman treats production configuration as a security boundary. Production
must fail closed when required configuration is absent rather than silently
falling back to development behaviour.

## Production mode

The packaged local systemd services set `JOURNEYMAN_CONFIG` to
`/etc/journeyman/journeyman.yml`. The YAML file selects
`app.config.ProductionConfig`; Journeyman loads and validates that file before
application configuration is imported. Values defined in
`/etc/journeyman/journeyman.yml` are authoritative and replace matching legacy
`JOURNEYMAN_*` environment values. `JOURNEYMAN_CONFIG` remains the bootstrap
selector for the YAML file itself. This prevents stale values from an older
`journeyman.env`, login shell, or service wrapper from silently overriding the
active YAML configuration.

Production configuration disables Flask debug mode and requires a unique
`JOURNEYMAN_SECRET_KEY`; the built-in development secret and documented
placeholder values are rejected. Production browser sessions use Secure,
HttpOnly and SameSite=Lax cookies.

The development `python run.py` entry point honours the selected configuration
rather than forcing Flask debug mode on.

## Service accounts and privileges

The packaged web, scheduler, environment-builder and local-runner units run as
the dedicated `journeyman` account rather than root. The web service uses
`NoNewPrivileges=true` and binds Gunicorn only to `127.0.0.1:5000`, behind the
local reverse proxy. The local runner intentionally has different privilege
requirements because Projects may perform privileged automation; those
privileges are part of the execution trust boundary and are not inherited by
the web process.

Database accounts and external-system credentials are deployment-specific and
must be granted only the permissions Journeyman needs. Journeyman does not ship
working default credentials for Satellite, Zabbix, LDAP, Git, PostgreSQL or
other backend services.

## Backend communication

Journeyman can communicate with systems configured by administrators,
including:

- PostgreSQL or SQLite;
- LDAP/Active Directory;
- Git repositories;
- Satellite/Foreman and Zabbix inventory services;
- local and remote runners;
- configured HTTP(S) package/proxy resources used for managed environments;
- the local privileged Nginx-configuration helper.

Several clients already enforce finite connection or operation timeouts.
However, Journeyman does not yet have one centrally documented/enforced policy
covering connection-pool limits, retry budgets, backoff and failure behaviour
for every backend. ASVS controls requiring that complete resource-management
model remain Deferred.

Journeyman also does not currently enforce an application-wide outbound network
allowlist. Administrators can deliberately configure repositories and inventory
endpoints. Network-layer egress policy should therefore be used in hardened
deployments; application-level allowlisting remains future hardening work.

## Secret configuration

Backend secrets are not to be committed to source control. Credential values
and managed-environment proxy passwords are encrypted at rest using the
Journeyman credential key. The key itself is external to the database and
source tree and must have mode 0600 or stricter. The fallback administrator
password hash is likewise stored outside the source tree.

Journeyman does not yet integrate with an external secrets vault/HSM and does
not currently provide a complete automatic rotation schedule for every secret.
Those ASVS controls remain Deferred rather than being treated as satisfied by
file-based encryption.

## Information leakage

Production debug mode is disabled. HTTP TRACE is not an application route and
must return Method Not Allowed. Administrative monitoring information such as
System Status is authorization protected. Generic 500 responses do not return
tracebacks to users.

Journeyman does intentionally display its own application version. It must not
expose unnecessary versions of Python, Flask, Werkzeug, Gunicorn, database
servers or other backend components through application responses.

Source-control metadata is a release/deployment concern. Development and local
source checkouts may contain `.git`; a release deployment intended to satisfy
ASVS V13.4.1 must use an artifact that omits source-control metadata or otherwise
makes it inaccessible to the application and web tier. This remains Deferred
until the release packaging/deployment process enforces it.

Nginx directory indexing is not required by Journeyman and should remain
disabled. A strict web-tier extension allowlist and explicit deployment-level
egress allowlist are not currently enforced by Journeyman and remain Deferred.

## Review evidence

Automated security regression tests cover production-mode defaults, packaged
systemd configuration, debug handling, secret placeholders, cookie security,
TRACE rejection and backend-version response headers. Manual/deployment controls
and known gaps are recorded explicitly in `ASVS_MATRIX.csv` rather than inferred
from framework defaults.
## Release artifact hygiene

Public/release source archives must be created with
`scripts/build_release_archive.py`. The builder explicitly excludes source-
control metadata, Flask runtime instance data, virtual environments, caches,
logs, bytecode, and previously built tarballs. Security tests verify that `.git`
and runtime state cannot enter an archive produced by this supported release
path. The managed Nginx configuration also explicitly disables directory
indexes (`autoindex off`).
