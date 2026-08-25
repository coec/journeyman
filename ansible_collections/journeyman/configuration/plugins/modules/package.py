"""Manage Journeyman Packages declaratively."""

DOCUMENTATION = r'''
module: package
short_description: Configure a Journeyman Package and its user inputs
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
  project:
    type: str
    description:
    - Name of the Journeyman Project.
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  allow_as_reaction:
    type: bool
    default: false
    description:
    - Whether this Package may be invoked by a Reactor.
  access_mode:
    type: str
    choices:
    - restricted
    - authenticated
    default: restricted
    description:
    - Package access-control mode.
  warning_message:
    type: str
    default: ''
    description:
    - Warning displayed before a user dispatches the Package.
  confirmation_required:
    type: bool
    default: true
    description:
    - Whether interactive Package dispatch requires explicit confirmation.
  confirmation_message:
    type: str
    default: ''
    description:
    - Confirmation text shown before interactive Package dispatch.
  fixed_vars:
    type: dict
    default: {}
    description:
    - Fixed extra variables supplied by the Package.
  inputs:
    type: list
    elements: dict
    default: []
    description:
    - Package input values keyed by declared Package variable name.
  permissions:
    type: list
    elements: dict
    default: []
    description:
    - Package permission assignments.
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
    - Base URL of the Journeyman server. Defaults to the C(JOURNEYMAN_URL) environment variable.
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
  api_token:
    type: str
    no_log: true
    description:
    - Journeyman API bearer token. Defaults to the C(JOURNEYMAN_API_TOKEN) environment variable.
description:
- Declares the desired configuration of a Journeyman Package.
- Packages expose a Project through fixed values, user inputs, access controls, and optional reaction
  use.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Configure a Package
  journeyman.configuration.package:
    name: Port Control
    project: Port Control
    inputs:
      - name: interface
        label: Interface
        type: text
        required: true
    state: present
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
package: {description: Resulting Package configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''



def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request("DELETE", "/api/v1/package-configurations/by-name", query={"name": name})
    payload = {
        "name": name,
        "description": params.get("description", ""),
        "project": params.get("project") or "",
        "enabled": params.get("enabled", True),
        "allow_as_reaction": params.get("allow_as_reaction", False),
        "access_mode": params.get("access_mode", "restricted"),
        "warning_message": params.get("warning_message", ""),
        "confirmation_required": params.get("confirmation_required", True),
        "confirmation_message": params.get("confirmation_message", ""),
        "fixed_vars": params.get("fixed_vars") or {},
        "inputs": params.get("inputs") or [],
        "permissions": params.get("permissions") or [],
    }
    return client.request("PUT", "/api/v1/package-configurations/by-name", payload=payload)


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import JourneymanApiClient, JourneymanApiError

    module = AnsibleModule(argument_spec={
        "name": {"type": "str", "required": True},
        "description": {"type": "str", "default": ""},
        "project": {"type": "str"},
        "enabled": {"type": "bool", "default": True},
        "allow_as_reaction": {"type": "bool", "default": False},
        "access_mode": {"type": "str", "choices": ["restricted", "authenticated"], "default": "restricted"},
        "warning_message": {"type": "str", "default": ""},
        "confirmation_required": {"type": "bool", "default": True},
        "confirmation_message": {"type": "str", "default": ""},
        "fixed_vars": {"type": "dict", "default": {}},
        "inputs": {"type": "list", "elements": "dict", "default": []},
        "permissions": {"type": "list", "elements": "dict", "default": []},
        "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
        "journeyman_url": {"type": "str"},
        "api_token": {"type": "str", "no_log": True},
        "validate_certs": {"type": "bool", "default": True},
        "timeout": {"type": "int", "default": 30},
    }, supports_check_mode=False)
    try:
        client = JourneymanApiClient(url=module.params["journeyman_url"], token=module.params["api_token"], validate_certs=module.params["validate_certs"], timeout=module.params["timeout"])
        module.exit_json(**execute(module.params, client))
    except JourneymanApiError as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
