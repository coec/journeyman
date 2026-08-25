# Journeyman

**Less platform. More automation.**

Journeyman is a native-execution automation platform for Git-backed Ansible
Projects, controlled user-facing Packages, dynamic inventories, credentials,
scheduling, auditing, and distributed execution.

Journeyman is licensed under the Apache License, Version 2.0. See LICENSE.

## Design principles

### Intentionally single-tenant

Journeyman is intentionally and explicitly single-tenant. It is designed for one
organisation and one trusted administrative domain. It is not intended to host
mutually untrusted tenants, and its security model must not be represented as a
multi-tenant isolation boundary.

### Intentionally private

Journeyman is intended for deployment on trusted internal networks. It should not
be exposed directly to the public Internet.

Journeyman is an automation and orchestration platform with the ability to execute
commands and playbooks, use stored credentials, modify managed systems, and
perform other privileged operations. A compromised Journeyman instance could
therefore have a significant impact on the systems it manages.

Deploy Journeyman behind appropriate network controls and restrict access to
trusted users and systems. Internet-facing deployment is unsupported and
strongly discouraged.

### Intentionally non-containerized

Journeyman is intentionally non-containerized. The application, local and remote
runners, and Python execution environments run directly on managed Linux systems
using systemd and Python virtual environments.

This is a deliberate operational design, not a temporary development limitation.

## Core concepts

- **Projects** define one or more ordered automation steps.
- **Packages** provide the controlled user-facing dispatch layer around Projects.
  A Package may have no inputs at all, or may combine prompted runtime inputs,
  fixed values, dispatch permissions, warnings, target preview, and confirmation.
- **Repositories** supply version-controlled playbooks.
- **Inventories** resolve execution targets from configured sources.
- **Credentials** are encrypted and snapshotted for queued Jobs.
  Machine and Windows credentials are separate types so a mixed Linux/Windows step
  can safely use both; Windows identity is exposed as `win_ansible_user` and
  `win_ansible_password` for explicit activation by the playbook. Machine and
  Windows credentials may also define credential-scoped extra vars using the
  restricted `{{ user }}` and `{{ passwd }}` placeholders; Journeyman renders
  these only in the private execution workspace. Custom credentials provide a
  restricted field-and-injector model for multi-value application credentials;
  see [`docs/CUSTOM_CREDENTIALS.md`](docs/CUSTOM_CREDENTIALS.md).
- **Environments** select System Ansible or a managed Python virtual environment.
- **Jobs** are immutable execution records.
- **Teams** map Active Directory groups to Package execution permissions.
- **Runners** execute queued Jobs locally and on remote nodes (not thoroughly 
  tested).

## Projects and Packages

For development or controlled execution of multi-step workflows, Projects can optionally require **Oversight between all steps**. Journeyman pauses between resolved execution batches and presents the next step(s), inventory, repository commit and runner destinations before continuing. See `docs/OVERSIGHT.md`.

Projects define **how automation runs**: workflow steps, dependencies,
repositories, inventories, credentials, execution environments, routing, and
failure handling.

Packages define **how a person is allowed to dispatch a Project**. A Package can
be a simple one-click dispatch with no prompts, or it can expose a controlled set
of runtime inputs while locking other values. Packages are intentionally the
single user-input/survey layer; Journeyman does not maintain a second,
competing Project-survey mechanism.

Package inputs currently support text, integer, boolean, choice, secret, and
email-address input types, including validation and conditions. Validation and
conditions are stored as YAML so the rules remain explicit and reviewable.
Examples are provided in the administration UI rather than hiding the
underlying rule syntax.

A normal Package input uses the **Runtime variable** binding. For Ansible
Projects the value is supplied as an Ansible extra variable. Script-based
execution receives declared Package input values through the
`JOURNEYMAN_INPUT_*` environment-variable convention. The **Step limit**
binding remains specific to an Ansible project step.

Package dispatch data is validated and previewed before execution. Secrets are
excluded from normal confirmation displays, and queued Jobs retain immutable
Package snapshots for auditability.

Example fixed values:

```yaml
maintenance_mode: true
```

Example text validation:

```yaml
minimum_length: 3
maximum_length: 40
pattern: '^[A-Za-z0-9._-]+$'
```

Example integer validation:

```yaml
minimum: 1
maximum: 300
```

Example conditional input:

```yaml
visible_when:
  action: shutnoshut
required_when:
  change_description: true
```

Conditions may reference only inputs that appear earlier in the Package form.

## Authentication and authorization

Journeyman authenticates users directly against Active Directory over LDAPS.
Configurable AD groups assign the Administrator and User roles.

Administrators manage Journeyman resources. Users cannot modify resources and
may execute only Packages granted directly to them or through registered AD
Teams.

A local break-glass `admin` account may be provisioned for emergency recovery.
Each activation is non-renewable, expires after 60 minutes, and expires
immediately when the fallback administrator signs out. Re-entry requires a fresh
server-admin CLI activation. Only a salted password hash is stored; fallback
provisioning, use, and expiry are recorded in the Audit Log.

## Secrets

Credential values, LDAP bind passwords, Package secret inputs, and other
recoverable secrets are encrypted before database storage using the configured
Journeyman encryption key.

The encryption key must be protected separately from the database and must not be
committed to the repository.

## Execution environments

Journeyman supports:

- **System Ansible**, resolved from the runner node's `PATH`.
- Registered existing Python virtual environments.
- Journeyman-managed virtual environments built by the dedicated environment
  builder service.

Selected environments are validated and snapshotted into queued Job steps.

## Services

A standard main-server installation enables only `journeyman.service`. It
coordinates the static child units `journeyman-web.service`,
`journeyman-scheduler.service`, `journeyman-runner.service`, and
`journeyman-environment-builder.service`. `journeyman-remote-runner.service`
remains independent on remote execution nodes.

Reference unit files are stored under `deploy/systemd/`.

## Storage planning

Journeyman may require substantial space under `/var`, particularly under
`/var/lib/journeyman`.

Storage usage depends on the size and contents of configured Git repositories,
the number of repositories, and the frequency of Project and Package
executions. Repository working copies, inventory snapshots, Job output, and
other immutable execution records can accumulate over time.

Administrators should size and monitor the filesystem accordingly, especially
where repositories contain large files or automation is executed frequently.

## Installation

Installation, upgrade, service configuration, database migration, encryption-key
setup, LDAP configuration, and recovery procedures are documented in
[`INSTALL.md`](INSTALL.md).

## Supported Operating systems

Journeyman has been developed and tested on RHEL9.8 with Python 3.14. Other
operating systems and python versions may work but are untested and will be
self-supported.

## Security scope

Journeyman is intended for trusted organisational automation. Applicable OWASP
ASVS requirements are tracked and verified through automated tests, manual 
review, deployment hardening, threat modelling, and independent penetration
testing.

Security policy, design principles, the threat model, and the ASVS coverage
framework are documented in [`SECURITY.md`](SECURITY.md) and
[`docs/security/`](docs/security/).

## Script Projects

Projects may execute either Ansible playbooks or repository-backed scripts.
A Project uses one execution type for all of its steps. Script files must be
regular files inside the immutable repository snapshot. Files ending in `.sh`
are accepted, and extensionless or other script files are accepted when they
begin with a shebang such as `#!/bin/bash`, `#!/usr/bin/perl`, or
`#!/usr/bin/python3`. Local Script execution honours the shebang when present
and falls back to `/bin/bash` for `.sh` files without one. The required
interpreter must exist on the selected runner. Arbitrary command text cannot be
entered in the UI. Script steps use the same dependency graph, parallel-step
limit, credential environment variables, cancellation handling, output
capture, and repository snapshotting as Ansible steps.


## Remote runners

### Execution slices

Journeyman uses the term **execution slice** (or simply **slice**) for the
portion of a single Job step assigned to one runner. If hosts in the same step
are routed to different runners, Journeyman splits that step into one slice per
runner. Each slice contains only the hosts that runner is responsible for and
can execute independently of the other slices.

For example, a three-host step may produce a local slice containing two hosts
and a remote slice containing the third host.

In Journeyman, **slice does not refer to a systemd slice or cgroup**.

A bundled administrative playbook at
`deploy/ansible/install-remote-runner.yml` installs and registers a remote
runner from a one-time token created on the **Runners** page. See `INSTALL.md`
for bootstrap variables and examples.


## Notifications

Reusable Email, Webhook and Syslog Notification Targets and lifecycle rules are documented in [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).

## REST API and Ansible collections

Journeyman exposes a versioned `/api/v1` interface for external automation.
The API is the supported external contract; Ansible collections are thin clients
of the same API rather than a second implementation path.

The `journeyman.operation` collection provides `dispatch`, `job_info`, and
`job_cancel`. `dispatch` supports Project and Package launches plus immutable
reruns of completed Jobs. The `journeyman.configuration` collection provides
idempotent modules for Repository, Credential, Inventory, Project, Package,
Schedule, Signal Source, and Reactor resources.

API bearer tokens are stored only as SHA-256 digests, have a maximum 12-month
lifetime, and warn their owner during the final 30 days before hard expiry. They
can be created with `scripts/journeyman-api-token create`. The v1 endpoint contract is documented in
[docs/API.md](docs/API.md).
