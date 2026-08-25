DOCUMENTATION = r'''
module: project
short_description: Configure a Journeyman Project and its workflow steps
options:
  name:
    type: str
    required: true
    description:
    - Name of the Journeyman resource.
  description:
    type: str
    default: ''
    description:
    - Human-readable description of the Journeyman resource.
  execution_type:
    type: str
    choices:
    - ansible
    - shell
    - remote_shell
    default: ansible
    description:
    - Execution backend used by the Project.
  inventory:
    type: str
    description:
    - Name of the default Journeyman Inventory used by the Project.
  repository:
    type: str
    description:
    - Name of the Journeyman Repository used by the Project.
  environment:
    type: str
    description:
    - Name of the Journeyman execution environment used by the Project.
  credentials:
    type: list
    elements: str
    default: []
    description:
    - Names of Journeyman Credentials attached to the Project.
  max_parallel_steps:
    type: int
    default: 4
    description:
    - Maximum number of workflow steps that may execute concurrently.
  concurrency_policy:
    type: str
    choices:
    - unrestricted
    - distinct_parameters
    - serialized
    - exclusive
    default: unrestricted
    description:
    - Project concurrency policy applied across all launch paths.
  oversight_required_between_all_steps:
    type: bool
    default: false
    description:
    - Whether operator oversight is required at every workflow-step boundary.
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  steps:
    type: list
    elements: dict
    description:
    - Ordered workflow step definitions for the Project.
  state:
    type: str
    choices:
    - present
    - absent
    default: present
    description:
    - Desired presence or absence of the Journeyman resource.
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
- Declares the desired configuration of a Journeyman Project and its workflow steps.
- Named dependencies and related resources are resolved by Journeyman.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Configure a Project
  journeyman.configuration.project:
    name: Verify hosts
    inventory: Linux
    steps:
      - name: Verify
        repository: Automation
        playbook: verify.yml
    state: present
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
project: {description: Resulting Project configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''



def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request(
            "DELETE",
            "/api/v1/project-configurations/by-name",
            query={"name": name},
        )

    payload = {
        "name": name,
        "description": params.get("description", ""),
        "execution_type": params.get("execution_type", "ansible"),
        "inventory": params.get("inventory") or "",
        "repository": params.get("repository") or "",
        "environment": params.get("environment") or "",
        "credentials": params.get("credentials") or [],
        "max_parallel_steps": params.get("max_parallel_steps", 4),
        "concurrency_policy": params.get("concurrency_policy", "unrestricted"),
        "oversight_required_between_all_steps": params.get(
            "oversight_required_between_all_steps",
            False,
        ),
        "enabled": params.get("enabled", True),
        "steps": params.get("steps") or [],
    }
    return client.request(
        "PUT",
        "/api/v1/project-configurations/by-name",
        payload=payload,
    )


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import (
        JourneymanApiClient,
        JourneymanApiError,
    )

    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "description": {"type": "str", "default": ""},
            "execution_type": {
                "type": "str",
                "choices": ["ansible", "shell", "remote_shell"],
                "default": "ansible",
            },
            "inventory": {"type": "str"},
            "repository": {"type": "str"},
            "environment": {"type": "str"},
            "credentials": {"type": "list", "elements": "str", "default": []},
            "max_parallel_steps": {"type": "int", "default": 4},
            "concurrency_policy": {
                "type": "str",
                "choices": ["unrestricted", "distinct_parameters", "serialized", "exclusive"],
                "default": "unrestricted",
            },
            "oversight_required_between_all_steps": {"type": "bool", "default": False},
            "enabled": {"type": "bool", "default": True},
            "steps": {"type": "list", "elements": "dict"},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
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
