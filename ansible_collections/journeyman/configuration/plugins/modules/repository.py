DOCUMENTATION = r'''
module: repository
short_description: Configure a Journeyman Repository
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
  repository_type:
    type: str
    choices:
    - git
    - directory
    default: git
    description:
    - Journeyman Repository type.
  url:
    type: str
    description:
    - Repository clone or access URL.
  directory_path:
    type: str
    description:
    - Filesystem path used by a directory Repository.
  default_branch:
    type: str
    default: main
    description:
    - Default Git branch for the Repository.
  credential:
    type: str
    description:
    - Name of the Journeyman Credential used by this resource.
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
- Declares the desired configuration of a Journeyman Repository.
- The resource is created, updated, or removed through the Journeyman management API.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Configure a Git repository with every module option
  journeyman.configuration.repository:
    name: Automation
    description: Main infrastructure automation repository
    repository_type: git
    url: https://git.example/automation.git
    directory_path: ''
    default_branch: main
    credential: Git service account
    state: present
    journeyman_url: https://journeyman.example/
    api_token: "{{ vault_journeyman_api_token }}"
    validate_certs: true
    timeout: 60

- name: Configure a local directory repository
  journeyman.configuration.repository:
    name: Local automation
    repository_type: directory
    directory_path: /srv/journeyman/automation
    state: present

- name: Remove a repository
  journeyman.configuration.repository:
    name: Retired repository
    state: absent
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
repository: {description: Resulting Repository configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''



def execute(params, client):
    name = params["name"]
    state = params.get("state", "present")
    if state == "absent":
        data = client.request(
            "DELETE",
            "/api/v1/repositories/by-name",
            query={"name": name},
        )
    else:
        payload = {
            "name": name,
            "description": params.get("description", ""),
            "repository_type": params.get("repository_type", "git"),
            "url": params.get("url") or "",
            "directory_path": params.get("directory_path") or "",
            "default_branch": params.get("default_branch", "main"),
            "credential": params.get("credential") or "",
        }
        data = client.request("PUT", "/api/v1/repositories/by-name", payload=payload)
    return data


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
            "repository_type": {"type": "str", "choices": ["git", "directory"], "default": "git"},
            "url": {"type": "str"},
            "directory_path": {"type": "str"},
            "default_branch": {"type": "str", "default": "main"},
            "credential": {"type": "str"},
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
