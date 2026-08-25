"""Manage Journeyman Inventories declaratively."""

DOCUMENTATION = r'''
module: inventory
short_description: Configure a Journeyman Inventory
options:
  name:
    type: str
    required: true
    description:
    - Name of the Journeyman resource.
  inventory_type:
    type: str
    choices:
    - satellite
    - static
    - filtered
    - composite
    - zabbix
    - netbox
    - lightspeed
    - ovirt
    default: static
    description:
    - Journeyman inventory type.
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  credential:
    type: str
    description:
    - Name of the Journeyman Credential used by this resource.
  verify_tls:
    type: bool
    default: true
    description:
    - Whether Journeyman validates TLS when contacting the inventory source.
  organization:
    type: str
    description:
    - Satellite or Foreman organization used by the inventory.
  content:
    type: str
    description:
    - Static inventory content.
  source_inventory:
    type: str
    description:
    - Name of the source Inventory used by a filtered Inventory.
  include_groups:
    type: list
    elements: dict
    default: []
    description:
    - Filter rules that include matching hosts or groups.
  exclude_groups:
    type: list
    elements: dict
    default: []
    description:
    - Filter rules that exclude matching hosts or groups.
  source_inventories:
    type: list
    elements: str
    default: []
    description:
    - Names of source Inventories combined by a composite Inventory.
  normalize_hostnames:
    type: str
    choices: [none, short, fqdn]
    default: none
    description:
    - Composite Inventory hostname normalization mode.
    - C(short) merges an unambiguous short-name/FQDN pair to the short name.
    - C(fqdn) merges an unambiguous pair to the FQDN.
  append_domain:
    type: str
    default: ''
    description:
    - Optional default DNS domain appended to unqualified hostnames for any Inventory type.
    - This changes only the resolved Inventory output; provider data and source Inventories are not modified.
    - Existing qualified hostnames are left unchanged and collisions are rejected.
  endpoint:
    type: str
    description:
    - Remote inventory source endpoint URL.
  tag_name:
    type: str
    description:
    - Zabbix host tag name used for inventory selection.
  tag_value:
    type: str
    default: journeyman
    description:
    - Zabbix host tag value used for inventory selection.
  include_disabled:
    type: bool
    default: false
    description:
    - Whether disabled source hosts are included.
  status:
    type: str
    description:
    - NetBox device status filter.
  tag:
    type: str
    description:
    - NetBox tag filter.
  site:
    type: str
    description:
    - NetBox site slug filter.
  role:
    type: str
    description:
    - NetBox device-role slug filter.
  tags:
    type: str
    description:
    - Red Hat Lightspeed Host Inventory API tags filter.
  query_filter:
    type: dict
    description:
    - Optional oVirt / RHV VM query filter passed to ovirt.ovirt.ovirt.
  hostname_preference:
    type: list
    elements: str
    description:
    - oVirt / RHV VM attributes used to choose inventory hostnames.
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
- Declares the desired configuration of a Journeyman Inventory.
- Supports static, Satellite, filtered, composite, Zabbix, NetBox, Red Hat Lightspeed, and oVirt / RHV inventory definitions.
version_added: 0.1.0
author:
- Journeyman contributors
'''

EXAMPLES = r'''
- name: Configure a static inventory
  journeyman.configuration.inventory:
    name: Lab
    inventory_type: static
    content: |
      [linux]
      host01
    state: present
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
inventory: {description: Resulting Inventory configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''

def execute(params, client):
    name = params["name"]
    if params.get("state", "present") == "absent":
        return client.request(
            "DELETE",
            "/api/v1/inventory-configurations/by-name",
            query={"name": name},
        )

    payload = {
        key: params.get(key)
        for key in (
            "name", "inventory_type", "enabled", "credential", "verify_tls",
            "organization", "content", "source_inventory", "include_groups",
            "exclude_groups", "source_inventories", "normalize_hostnames", "append_domain", "endpoint", "tag_name",
            "tag_value", "include_disabled", "status", "tag", "site", "role", "interfaces", "services", "config_context", "site_data", "virtual_disks", "tags", "proxy_credential",
        )
        if key in params and params.get(key) is not None
    }
    return client.request("PUT", "/api/v1/inventory-configurations/by-name", payload=payload)


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import (
        JourneymanApiClient,
        JourneymanApiError,
    )

    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "inventory_type": {
                "type": "str",
                "choices": ["satellite", "static", "filtered", "composite", "zabbix", "netbox", "lightspeed", "ovirt"],
                "default": "static",
            },
            "enabled": {"type": "bool", "default": True},
            "credential": {"type": "str"},
            "verify_tls": {"type": "bool", "default": True},
            "organization": {"type": "str"},
            "content": {"type": "str"},
            "source_inventory": {"type": "str"},
            "include_groups": {"type": "list", "elements": "dict", "default": []},
            "exclude_groups": {"type": "list", "elements": "dict", "default": []},
            "source_inventories": {"type": "list", "elements": "str", "default": []},
            "normalize_hostnames": {"type": "str", "choices": ["none", "short", "fqdn"], "default": "none"},
            "append_domain": {"type": "str", "default": ""},
            "endpoint": {"type": "str"},
            "tag_name": {"type": "str"},
            "tag_value": {"type": "str", "default": "journeyman"},
            "include_disabled": {"type": "bool", "default": False},
            "status": {"type": "str"},
            "tag": {"type": "str"},
            "site": {"type": "str"},
            "role": {"type": "str"},
            "interfaces": {"type": "bool"},
            "services": {"type": "bool"},
            "config_context": {"type": "bool"},
            "site_data": {"type": "bool"},
            "virtual_disks": {"type": "bool"},
            "tags": {"type": "str"},
            "query_filter": {"type": "dict"},
            "hostname_preference": {"type": "list", "elements": "str"},
            "proxy_credential": {"type": "str"},
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
