"""Manage Journeyman Reactors declaratively."""

DOCUMENTATION = r'''
module: reactor
short_description: Configure a Journeyman Reactor
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
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  mode:
    type: str
    choices:
    - observe
    - automatic
    default: observe
    description:
    - Reactor execution mode.
  source:
    type: str
    required: true
    description:
    - Name of the Signal Source consumed by the Reactor.
  package:
    type: str
    required: true
    description:
    - Name of the reaction-enabled Package dispatched by the Reactor.
  match:
    type: dict
    default:
      all: []
    description:
    - Signal matching rules that determine whether the Reactor fires.
  mappings:
    type: dict
    default: {}
    description:
    - Mappings from Signal fields into Package reaction inputs.
  recovery_window_seconds:
    type: int
    default: 0
    description:
    - Time window in seconds in which a matching recovery Signal suppresses the reaction.
  recovery_match:
    type: dict
    default:
      all: []
    description:
    - Matching rules that identify a recovery Signal.
  recovery_correlation_inputs:
    type: list
    elements: str
    default: []
    description:
    - Signal fields used to correlate problem and recovery Signals.
  cooldown_seconds:
    type: int
    default: 0
    description:
    - Minimum cooldown period in seconds between matching reactions.
  max_concurrency:
    type: int
    default: 1
    description:
    - Maximum number of concurrent Reactions allowed for this Reactor.
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
    - Journeyman base URL; defaults to JOURNEYMAN_URL.
  api_token:
    type: str
    no_log: true
    description:
    - Journeyman API bearer token; defaults to JOURNEYMAN_API_TOKEN.
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
- Declares the desired configuration of a Journeyman Reactor.
- Reactors match Signals to reaction-enabled Packages and define input mappings and safety controls.
version_added: 0.1.0
author:
- Journeyman contributors
'''

EXAMPLES = r'''
- name: Configure a fully specified automatic Reactor
  journeyman.configuration.reactor:
    name: Recover GRE tunnel
    description: Recover a failed GRE tunnel when Zabbix reports it down
    enabled: true
    mode: automatic
    source: Zabbix production
    package: Recover GRE Tunnel
    match:
      all:
        - field: event_name
          operator: contains
          value: GRE tunnel down
        - field: severity
          operator: in
          value:
            - High
            - Disaster
    mappings:
      hostname: host
      tunnel_name: tunnel
    recovery_window_seconds: 120
    recovery_match:
      all:
        - field: event_name
          operator: contains
          value: GRE tunnel recovered
    recovery_correlation_inputs:
      - hostname
      - tunnel_name
    cooldown_seconds: 300
    max_concurrency: 2
    state: present
    journeyman_url: https://journeyman.example/
    api_token: "{{ vault_journeyman_api_token }}"
    validate_certs: true
    timeout: 60

- name: Remove a Reactor
  journeyman.configuration.reactor:
    name: Retired Reactor
    source: Zabbix production
    package: Recover GRE Tunnel
    state: absent
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
reactor: {description: Resulting Reactor configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''

def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request("DELETE", "/api/v1/reactor-configurations/by-name", query={"name": name})
    payload = {
        key: params.get(key)
        for key in (
            "name", "description", "enabled", "mode", "source", "package", "match", "mappings",
            "recovery_window_seconds", "recovery_match", "recovery_correlation_inputs",
            "cooldown_seconds", "max_concurrency",
        )
        if key in params and params.get(key) is not None
    }
    return client.request("PUT", "/api/v1/reactor-configurations/by-name", payload=payload)


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import JourneymanApiClient, JourneymanApiError
    module = AnsibleModule(argument_spec={
        "name": {"type": "str", "required": True}, "description": {"type": "str", "default": ""},
        "enabled": {"type": "bool", "default": True}, "mode": {"type": "str", "choices": ["observe", "automatic"], "default": "observe"},
        "source": {"type": "str", "required": True}, "package": {"type": "str", "required": True},
        "match": {"type": "dict", "default": {"all": []}}, "mappings": {"type": "dict", "default": {}},
        "recovery_window_seconds": {"type": "int", "default": 0}, "recovery_match": {"type": "dict", "default": {"all": []}},
        "recovery_correlation_inputs": {"type": "list", "elements": "str", "default": []},
        "cooldown_seconds": {"type": "int", "default": 0}, "max_concurrency": {"type": "int", "default": 1},
        "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
        "journeyman_url": {"type": "str"}, "api_token": {"type": "str", "no_log": True},
        "validate_certs": {"type": "bool", "default": True}, "timeout": {"type": "int", "default": 30},
    }, supports_check_mode=False)
    try:
        client = JourneymanApiClient(url=module.params["journeyman_url"], token=module.params["api_token"], validate_certs=module.params["validate_certs"], timeout=module.params["timeout"])
        module.exit_json(**execute(module.params, client))
    except JourneymanApiError as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
