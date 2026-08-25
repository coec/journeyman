# journeyman.operation

Operational Ansible modules backed exclusively by Journeyman's versioned `/api/v1` REST API.

## Modules

- `journeyman.operation.dispatch` — dispatch a Project, dispatch a Package, or rerun a completed Job from its immutable execution snapshots.
- `journeyman.operation.job_info` — retrieve Job state and step status.
- `journeyman.operation.job_cancel` — cancel or stop an eligible Job.

## Connection

All modules accept:

- `journeyman_url` — defaults to `JOURNEYMAN_URL`.
- `api_token` — defaults to `JOURNEYMAN_API_TOKEN` and is treated as secret.
- `validate_certs` — defaults to `true`.
- `timeout` — HTTP timeout in seconds, default `30`.

Create API tokens with Journeyman's `scripts/journeyman-api-token` utility. Use HTTPS for non-local connections.

## Dispatch

```yaml
- name: Dispatch Project
  journeyman.operation.dispatch:
    type: project
    name: Provision VM
```

```yaml
- name: Dispatch Package
  journeyman.operation.dispatch:
    type: package
    name: Cisco Port Control
    inputs:
      interface: GigabitEthernet1/0/10
      state: up
```

```yaml
- name: Rerun completed Job
  journeyman.operation.dispatch:
    type: job
    job_id: 1234
```

A Job rerun creates a new Job from the source Job's immutable execution snapshots. It does not reinterpret the source Job using current Project or Package configuration.

Argument combinations are intentionally strict:

- `type: project` requires `name`; `job_id` and `inputs` are invalid.
- `type: package` requires `name`; `inputs` are optional and `job_id` is invalid.
- `type: job` requires `job_id`; `name` and `inputs` are invalid.

Mutating operation modules do not support Ansible check mode because dispatch/cancel operations have no meaningful non-mutating equivalent. `job_info` is read-only and supports check mode.
