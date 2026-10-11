# Execution Environment Dependencies

Journeyman execution Environments can contain three dependency layers:

1. remote-runner operating-system packages;
2. Python packages installed with `pip`; and
3. Ansible collections installed with `ansible-galaxy`.

## Remote runner system packages

The **Remote runner system packages** field accepts one RPM/DNF package name per
line. These requirements are installed by **Manage Remote Runner** before an
Environment is synchronized to that runner. Environment synchronization itself
remains unprivileged and refuses to continue when a declared runner package is
missing.

These declarations apply to remote execution runners only. Journeyman does not
automatically install operating-system packages on the Journeyman controller.
Keeping controller OS package management outside the Environment builder avoids
giving an application-controlled build process authority to modify the host OS.

## Python packages that need OS dependencies

A Python package can be valid pip input but still require an operating-system
library, header package, compiler, or other build tool. When that happens, pip
may report missing headers, missing libraries, `pkg-config` failures, compiler
errors, or a failure while building a wheel.

Install the required RPM/DNF dependencies on the Journeyman controller using
the site's normal operating-system management process, then rebuild the
Environment.

Common examples on RHEL-family systems include:

| Python package or feature | OS packages that may be required when building from source |
| --- | --- |
| Kerberos Python bindings such as `pykerberos` | `krb5-devel`, `gcc`, `python3-devel` |
| `python-ldap` | `openldap-devel`, `cyrus-sasl-devel`, `gcc`, `python3-devel` |
| `psycopg2` | `postgresql-devel`, `gcc`, `python3-devel` |
| `lxml` | `libxml2-devel`, `libxslt-devel`, `gcc`, `python3-devel` |
| Python packages containing C extensions | commonly `gcc` and `python3-devel`, plus the development package for the library being wrapped |

The exact requirements vary by Python package version, Python version, RHEL
release, architecture, and whether pip can use a pre-built wheel. Treat this
table as troubleshooting guidance rather than a complete dependency list; use
the Python package's upstream installation documentation as the authoritative
source.

## Build failure guidance

When pip fails during a managed Environment build or a registered Environment
dependency update, Journeyman adds a reminder to the build output that missing
controller OS dependencies may be the cause. The original pip error remains in
the output and should be used to identify the actual prerequisite.

## System package lifecycle

Removing an Environment from Journeyman does not remove any operating-system packages 
that were installed on remote runners to support that Environment. Runner system 
packages are treated as host-level prerequisites and are not automatically removed,
because they may also be required by other Environments or by the runner itself.

## Project Git role dependencies (`roles/requirements.yml`)

Journeyman supports the AAP convention of a Project containing
`roles/requirements.yml`. Ansible Galaxy/Git role dependencies in that file
are installed into the **individual Job snapshot**, rather than into a shared
`~/.ansible/roles` directory. This applies to the built-in runner and to remote
runner repository archives; remote runners do not need GitLab access to fetch
these roles. An absent or empty requirements file needs no action.

On the **Journeyman controller**, `ansible-galaxy` must be on the service's PATH
(or set `execution.role_galaxy_executable` to its full path in
`/etc/journeyman/journeyman.yml`). The service account must have noninteractive read access to
all private role Git sources, such as through an SSH deploy key and trusted SSH
host keys. Install/check the executable and credentials before dispatching a
Job. Git destinations in role requirements are subject to Journeyman's outbound
allowlist and secure transport policy; local file paths and unsupported SCM
transports are rejected.

Supported role sources are Galaxy role names and Git role URLs using `scm: git`
(HTTPS or SSH). The controller installs role dependencies on first preparation
of a Job's artefact and archives the resolved result with the pinned Project
commit. Retried claims for the same Job reuse the already-generated artefact.
If a dependency cannot be fetched, the Job fails clearly rather than silently
running without a required role. Mutable branch refs such as `master` are
resolved when the Job archive is first prepared; for reproducible role content,
use role Git commit hashes or immutable tags in the requirements file.

The first dispatch of a Project using private role requirements may take longer
while the controller fetches the roles. Role resolution has a configurable
`execution.role_install_timeout_seconds` timeout (default: 900 seconds). Because
resolution currently occurs during remote Job claim, allocate sufficient web
worker request time or migrate preparation to an asynchronous worker for large
repositories with many dependencies.
