DOCUMENTATION = r'''
module: credential
short_description: Configure a Journeyman Credential
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
  credential_type:
    type: str
    choices:
    - machine
    - windows
    - environment_variables
    - vault
    - source_control
    - satellite
    - zabbix
    - url
    - custom
    default: machine
    description:
    - Journeyman credential type.
  security_scope:
    type: str
    choices:
    - private
    - shared
    - public
    default: private
    description:
    - Visibility and ownership scope for the Credential.
  username:
    type: str
    default: ''
    description:
    - Username associated with the Credential where applicable.
  credential_data:
    type: dict
    no_log: true
    description:
    - Type-specific credential data.
    - Omitted secret fields are retained when updating an existing credential.
    - Non-secret fields are declarative and are removed when omitted.
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
- Declares the desired configuration of a Journeyman Credential.
- Secret values are sent to Journeyman but are never returned by the API.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Configure a source-control credential
  journeyman.configuration.credential:
    name: GitLab
    credential_type: source_control
    username: automation
    credential_data:
      password: "{{ vault_gitlab_token }}"
    state: present
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
credential: {description: Resulting non-secret Credential configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''



def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request(
            "DELETE", "/api/v1/credential-configurations/by-name", query={"name": name}
        )
    payload = {
        "name": name,
        "description": params.get("description", ""),
        "credential_type": params.get("credential_type", "machine"),
        "security_scope": params.get("security_scope", "private"),
        "username": params.get("username", ""),
        "credential_data": params.get("credential_data") or {},
    }
    return client.request("PUT", "/api/v1/credential-configurations/by-name", payload=payload)


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
            "credential_type": {
                "type": "str",
                "choices": ["machine", "windows", "environment_variables", "vault", "source_control", "satellite", "zabbix", "url", "custom"],
                "default": "machine",
            },
            "security_scope": {"type": "str", "choices": ["private", "shared", "public"], "default": "private"},
            "username": {"type": "str", "default": ""},
            "credential_data": {"type": "dict", "no_log": True},
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
