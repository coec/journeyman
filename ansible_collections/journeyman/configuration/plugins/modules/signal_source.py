"""Manage Journeyman Signal Sources declaratively."""

DOCUMENTATION = r'''
module: signal_source
short_description: Configure a Journeyman Signal Source
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
  source_type:
    type: str
    choices:
    - zabbix
    - syslog
    - snmp_trap
    default: zabbix
    description:
    - Signal Source transport or integration type.
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  allowed_networks:
    type: list
    elements: str
    default: []
    description:
    - Networks permitted to submit Signals to this Source.
  zabbix_url:
    type: str
    description:
    - Base URL associated with a Zabbix Signal Source.
  runner:
    type: str
    description:
    - Name of the Journeyman Runner that receives this Source's traffic.
  snmp_port:
    type: int
    default: 162
    description:
    - UDP port used by an SNMP trap Signal Source.
  hmac_secret:
    type: str
    no_log: true
    description:
    - Shared HMAC secret used to authenticate Zabbix Signals.
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
- Declares the desired configuration of a Journeyman Signal Source.
- Supports Zabbix, syslog, and SNMP trap signal ingestion settings.
version_added: 0.1.0
author:
- Journeyman contributors
'''

EXAMPLES = r'''
- name: Configure a Zabbix Signal Source
  journeyman.configuration.signal_source:
    name: Zabbix production
    description: Signed webhook Signals from production Zabbix
    source_type: zabbix
    enabled: true
    allowed_networks:
      - 192.0.2.0/24
      - 2001:db8:100::/64
    zabbix_url: https://zabbix.example/
    runner: Bentley runner
    hmac_secret: "{{ vault_journeyman_zabbix_hmac_secret }}"
    state: present
    journeyman_url: https://journeyman.example/
    api_token: "{{ vault_journeyman_api_token }}"
    validate_certs: true
    timeout: 60

- name: Configure an SNMP trap Signal Source
  journeyman.configuration.signal_source:
    name: Network traps
    description: SNMP traps received by the site runner
    source_type: snmp_trap
    enabled: true
    allowed_networks:
      - 198.51.100.0/24
    runner: Karratha runner
    snmp_port: 1162
    state: present

- name: Configure a syslog Signal Source
  journeyman.configuration.signal_source:
    name: Appliance syslog
    source_type: syslog
    enabled: true
    allowed_networks:
      - 203.0.113.0/24
    runner: Bentley runner
    state: present

- name: Remove a Signal Source
  journeyman.configuration.signal_source:
    name: Retired source
    state: absent
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
signal_source: {description: Resulting Signal Source configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''

def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request("DELETE", "/api/v1/signal-source-configurations/by-name", query={"name": name})
    payload = {
        key: params.get(key)
        for key in (
            "name", "description", "source_type", "enabled", "allowed_networks",
            "zabbix_url", "runner", "snmp_port", "hmac_secret",
        )
        if key in params and params.get(key) is not None
    }
    return client.request("PUT", "/api/v1/signal-source-configurations/by-name", payload=payload)


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import JourneymanApiClient, JourneymanApiError
    module = AnsibleModule(argument_spec={
        "name": {"type": "str", "required": True},
        "description": {"type": "str", "default": ""},
        "source_type": {"type": "str", "choices": ["zabbix", "syslog", "snmp_trap"], "default": "zabbix"},
        "enabled": {"type": "bool", "default": True},
        "allowed_networks": {"type": "list", "elements": "str", "default": []},
        "zabbix_url": {"type": "str"},
        "runner": {"type": "str"},
        "snmp_port": {"type": "int", "default": 162},
        "hmac_secret": {"type": "str", "no_log": True},
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
