# Journeyman Ansible Collections

Journeyman provides two Ansible collections:

- `journeyman.operation` — dispatch and inspect Journeyman Jobs.
- `journeyman.configuration` — declaratively manage Journeyman configuration.

The collections use Journeyman's versioned `/api/v1` REST API. They are intended
to be installed on an Ansible control node; they do not need to be installed in
the Journeyman application virtual environment unless that host is also being
used as an Ansible control node.

## Requirements

The control node requires:

- a supported `ansible-core` / Ansible installation with collection support;
- Python supported by that Ansible installation;
- HTTPS connectivity to the Journeyman server;
- a Journeyman API token with sufficient permissions for the requested action.

The Journeyman collections do **not** require additional third-party Python
modules beyond Ansible itself. Their HTTP client uses the Python standard
library.

`journeyman.configuration` has one Ansible collection dependency:

```text
journeyman.operation >= 1.0.0
```

Install `journeyman.operation` first when working in an isolated environment.

## Build from Journeyman source

From the root of a Journeyman source checkout:

```text
cd ansible_collections/journeyman/operation
ansible-galaxy collection build
```

This creates:

```text
journeyman-operation-1.0.0.tar.gz
```

Then build the configuration collection:

```text
cd ../configuration
ansible-galaxy collection build
```

This creates:

```text
journeyman-configuration-1.0.0.tar.gz
```

The collection version is taken from each collection's `galaxy.yml`.

For transfer to another host, copy both generated collection archives. The
generated archives are build artifacts and should not be committed to the
Journeyman Git repository.

## Install

Install the operation collection first:

```text
ansible-galaxy collection install \
  journeyman-operation-1.0.0.tar.gz
```

Then install the configuration collection:

```text
ansible-galaxy collection install \
  journeyman-configuration-1.0.0.tar.gz
```

For an upgrade or a rebuild of the same development version, use `--force`:

```text
ansible-galaxy collection install \
  journeyman-operation-1.0.0.tar.gz \
  --force

ansible-galaxy collection install \
  journeyman-configuration-1.0.0.tar.gz \
  --force
```

By default, `ansible-galaxy` installs collections into the first writable
collection path configured for the current Ansible user. Use
`ansible-galaxy collection list` to confirm the effective installation path.

## Verify the installation

List the installed Journeyman collections:

```text
ansible-galaxy collection list | grep journeyman
```

Expected versions for the Journeyman 1.0.0 release are:

```text
journeyman.configuration    1.0.0
journeyman.operation        1.0.0
```

Verify that Ansible can load the module documentation:

```text
ansible-doc journeyman.operation.dispatch
ansible-doc journeyman.configuration.inventory
```

If `ansible-doc` reports that a module cannot be found, check the collection
installation path reported by `ansible-galaxy collection list` and the
controller's configured Ansible collection paths.

## Connection configuration

All Journeyman collection modules support these connection arguments:

- `journeyman_url` — base URL of the Journeyman server;
- `api_token` — Journeyman API bearer token;
- `validate_certs` — validate the Journeyman TLS certificate, default `true`;
- `timeout` — HTTP request timeout in seconds, default `30`.

The URL and token can be supplied through environment variables instead of being
repeated in playbooks:

```text
export JOURNEYMAN_URL='https://journeyman.example.test'
export JOURNEYMAN_API_TOKEN='<token>'
```

Do not store API tokens in plaintext playbooks or source-control repositories.
Use the normal Ansible secret-management mechanism for the environment in which
the collection is being run.

The Ansible control node must trust the CA that issued the Journeyman HTTPS
certificate when `validate_certs: true` is used.

## Operation examples

Dispatch a Project:

```yaml
---
- name: Dispatch Journeyman Project
  hosts: localhost
  gather_facts: false

  tasks:
    - name: Dispatch Project
      journeyman.operation.dispatch:
        type: project
        name: Verify hosts
      register: dispatch_result
```

Dispatch a Package with inputs:

```yaml
- name: Dispatch Package
  journeyman.operation.dispatch:
    type: package
    name: Network Port Control
    inputs:
      interface: GigabitEthernet1/0/10
      state: up
```

Rerun an existing terminal Job from its immutable execution snapshots:

```yaml
- name: Rerun Job
  journeyman.operation.dispatch:
    type: job
    job_id: 1234
```

## Configuration example

```yaml
---
- name: Configure Journeyman
  hosts: localhost
  gather_facts: false

  tasks:
    - name: Configure automation repository
      journeyman.configuration.repository:
        name: Automation
        repository_type: git
        url: https://git.example.test/automation.git
        default_branch: main
        state: present
```

The configuration collection also provides modules for Credentials,
Inventories, Projects, Packages, Schedules, Signal Sources, and Reactors.

## Permissions

API permissions are enforced by Journeyman, not by the Ansible collection.

Configuration operations normally require an Administrator API token.
Operational dispatch permissions depend on the owning user's Journeyman role
and the access controls of the target Project or Package.

The collection never bypasses Journeyman authorization checks.

## Collection compatibility

Beginning with Journeyman 1.0.0, the `journeyman.operation` and
`journeyman.configuration` 1.x interfaces are part of Journeyman's supported
compatibility contract.

Backward-compatible collection functionality may be added in later minor
versions. Incompatible module/argument changes require a new major collection
version.
