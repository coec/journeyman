# journeyman.configuration

Declarative, idempotent configuration modules backed exclusively by Journeyman's versioned `/api/v1` REST API.

## Modules

- `journeyman.configuration.repository`
- `journeyman.configuration.credential`
- `journeyman.configuration.inventory`
- `journeyman.configuration.project`
- `journeyman.configuration.package`
- `journeyman.configuration.schedule`
- `journeyman.configuration.signal_source`
- `journeyman.configuration.reactor`

## Connection

All modules accept:

- `journeyman_url` — defaults to `JOURNEYMAN_URL`.
- `api_token` — defaults to `JOURNEYMAN_API_TOKEN` and is treated as secret.
- `validate_certs` — defaults to `true`.
- `timeout` — HTTP timeout in seconds, default `30`.

The collection declares a dependency on `journeyman.operation >= 1.0.0` because both collections share the same REST API client.

## Idempotency and secrets

Configuration modules use `state: present|absent` and return `changed: false` when the requested state already matches Journeyman. Stored credential secrets and Signal Source HMAC secrets are never returned by the REST API. Omitted secret fields are retained on updates where supported.

Mutating configuration modules do **not** claim Ansible check-mode support in v1.0.0. Server-side configuration operations are transactional/idempotent, but a true dry-run contract has not yet been implemented.

## Examples

```yaml
- name: Configure automation repository
  journeyman.configuration.repository:
    name: SysAdmin
    repository_type: git
    url: https://gitlab.example/sysadmin/ansible.git
    default_branch: main
    credential: GitLab
    state: present
```

```yaml
- name: Configure GitLab credential
  journeyman.configuration.credential:
    name: GitLab
    credential_type: source_control
    username: automation-user
    credential_data:
      password: "{{ vault_gitlab_access_token }}"
    state: present
```

```yaml
- name: Configure Project
  journeyman.configuration.project:
    name: Verify hosts
    inventory: Linux
    steps:
      - name: Verify
        repository: SysAdmin
        playbook: verify.yml
    state: present
```

```yaml
- name: Configure Zabbix Signal Source
  journeyman.configuration.signal_source:
    name: Zabbix Lab
    source_type: zabbix
    zabbix_url: https://zabbix.example/
    hmac_secret: "{{ vault_journeyman_zabbix_hmac_secret }}"
    state: present
```

```yaml
- name: Configure Reactor
  journeyman.configuration.reactor:
    name: GRE tunnel recovery
    source: Zabbix TRN02
    package: Recover GRE Tunnel
    mode: automatic
    cooldown_seconds: 300
    state: present
```
