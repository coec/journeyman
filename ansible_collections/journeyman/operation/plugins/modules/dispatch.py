DOCUMENTATION = r'''
module: dispatch
short_description: Dispatch a Journeyman Project, Package, or Job rerun
options:
  type:
    type: str
    required: true
    choices:
    - project
    - package
    - job
    description:
    - Kind of Journeyman object to dispatch.
  name:
    type: str
    description:
    - Project or Package name.
    - Required for C(type=project) and C(type=package).
    - Not valid for C(type=job).
  job_id:
    type: int
    description:
    - Existing Job ID to rerun.
    - Required for C(type=job).
    - Not valid for Project or Package dispatch.
  inputs:
    type: dict
    default: {}
    description:
    - Package input values keyed by declared Package variable name.
    - Only valid for C(type=package).
  rerun_scope:
    type: str
    default: all
    choices:
    - all
    - failed
    description:
    - Host scope for C(type=job) reruns.
    - C(failed) reruns only hosts whose final saved result was failed or unreachable.
  journeyman_url:
    type: str
    description:
    - Journeyman base URL.
    - Defaults to C(JOURNEYMAN_URL).
  api_token:
    type: str
    no_log: true
    description:
    - Journeyman API bearer token.
    - Defaults to C(JOURNEYMAN_API_TOKEN).
  validate_certs:
    type: bool
    default: true
    description:
    - Whether to validate the TLS certificate presented by the Journeyman server.
  timeout:
    type: int
    default: 30
    description:
    - HTTP request timeout in seconds.
description:
- Dispatches a Journeyman Project or Package, or reruns an existing terminal Job.
- Job reruns use the immutable execution snapshots captured by the source Job.
version_added: 0.1.0
author:
- Journeyman contributors
'''

EXAMPLES = r'''
- name: Dispatch a Project
  journeyman.operation.dispatch:
    type: project
    name: Patch Servers

- name: Dispatch a Package
  journeyman.operation.dispatch:
    type: package
    name: Cisco Port Control
    inputs:
      interface: GigabitEthernet1/0/10
      state: up

- name: Rerun an existing Job from its immutable snapshots
  journeyman.operation.dispatch:
    type: job
    job_id: 1234

- name: Rerun only failed or unreachable hosts
  journeyman.operation.dispatch:
    type: job
    job_id: 1234
    rerun_scope: failed
'''

RETURN = r'''
job:
  description: The newly queued Journeyman Job.
  returned: always
  type: dict
source_job_id:
  description: Source Job ID when C(type=job) is used.
  returned: when type is job
  type: int
'''


def _validation_error(message):
    from ansible_collections.journeyman.operation.plugins.module_utils.journeyman_api import JourneymanApiError
    raise JourneymanApiError(message)


def execute(params, client):
    dispatch_type = str(params.get("type") or "").strip().lower()
    name = str(params.get("name") or "").strip()
    job_id = params.get("job_id")
    inputs = params.get("inputs") or {}
    rerun_scope = str(params.get("rerun_scope") or "all").strip().lower()

    if dispatch_type not in {"project", "package", "job"}:
        _validation_error("type must be project, package, or job.")

    if dispatch_type == "project":
        if not name:
            _validation_error("name is required when type=project.")
        if job_id is not None:
            _validation_error("job_id is not valid when type=project.")
        if inputs:
            _validation_error("inputs are only valid when type=package.")
        if rerun_scope != "all":
            _validation_error("rerun_scope is only valid when type=job.")
        project = client.project_by_name(name)
        result = client.request(
            "POST", "/api/v1/projects/{}/dispatch".format(project["id"]), payload={}
        )
        return {"changed": True, "job": result["job"]}

    if dispatch_type == "package":
        if not name:
            _validation_error("name is required when type=package.")
        if job_id is not None:
            _validation_error("job_id is not valid when type=package.")
        if rerun_scope != "all":
            _validation_error("rerun_scope is only valid when type=job.")
        package = client.package_by_name(name)
        result = client.request(
            "POST",
            "/api/v1/packages/{}/dispatch".format(package["id"]),
            payload={"inputs": inputs},
        )
        return {"changed": True, "job": result["job"]}

    if name:
        _validation_error("name is not valid when type=job.")
    if inputs:
        _validation_error("inputs are not valid when type=job.")
    if job_id is None:
        _validation_error("job_id is required when type=job.")

    if rerun_scope not in {"all", "failed"}:
        _validation_error("rerun_scope must be all or failed.")

    result = client.request(
        "POST",
        "/api/v1/jobs/{}/rerun".format(job_id),
        payload={"scope": rerun_scope},
    )
    return {
        "changed": True,
        "source_job_id": result.get("source_job_id", job_id),
        "job": result["job"],
    }


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.operation.plugins.module_utils.journeyman_api import (
        JourneymanApiClient,
        JourneymanApiError,
    )

    module = AnsibleModule(
        argument_spec={
            "type": {"type": "str", "required": True, "choices": ["project", "package", "job"]},
            "name": {"type": "str"},
            "job_id": {"type": "int"},
            "inputs": {"type": "dict", "default": {}},
            "rerun_scope": {"type": "str", "default": "all", "choices": ["all", "failed"]},
            "journeyman_url": {"type": "str"},
            "api_token": {"type": "str", "no_log": True},
            "validate_certs": {"type": "bool", "default": True},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=False,
    )
    try:
        client = JourneymanApiClient(
            url=module.params["journeyman_url"],
            token=module.params["api_token"],
            validate_certs=module.params["validate_certs"],
            timeout=module.params["timeout"],
        )
        module.exit_json(**execute(module.params, client))
    except JourneymanApiError as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
