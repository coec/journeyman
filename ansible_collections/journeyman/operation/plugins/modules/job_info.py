DOCUMENTATION = r'''
module: job_info
short_description: Read a Journeyman Job
options:
  job_id:
    type: int
    required: true
    description:
    - Numeric Journeyman Job identifier.
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
- Retrieves the current state and details of a Journeyman Job.
- This module is read-only and can be used in Ansible check mode.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Read a Job
  journeyman.operation.job_info:
    job_id: 1234
  register: result
'''

RETURN = r'''
job:
  description: Journeyman Job status and step summary.
  returned: always
  type: dict
'''



def execute(params, client):
    result = client.request("GET", "/api/v1/jobs/{}".format(params["job_id"]))
    return {"changed": False, "job": result["job"]}


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.operation.plugins.module_utils.journeyman_api import JourneymanApiClient, JourneymanApiError
    module = AnsibleModule(argument_spec={
        "job_id": {"type": "int", "required": True},
        "journeyman_url": {"type": "str"},
        "api_token": {"type": "str", "no_log": True},
        "validate_certs": {"type": "bool", "default": True},
        "timeout": {"type": "int", "default": 30},
    }, supports_check_mode=True)
    try:
        client = JourneymanApiClient(url=module.params["journeyman_url"], token=module.params["api_token"], validate_certs=module.params["validate_certs"], timeout=module.params["timeout"])
        module.exit_json(**execute(module.params, client))
    except JourneymanApiError as exc:
        module.fail_json(msg=str(exc))

if __name__ == "__main__":
    main()
