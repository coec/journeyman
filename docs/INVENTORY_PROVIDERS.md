# Inventory providers

## NetBox

Create a URL / API credential whose base URL is the NetBox server and whose authentication mode is **Token**. Journeyman delegates discovery to the upstream `netbox.netbox.nb_inventory` Ansible inventory plugin and supplies `NETBOX_API` and `NETBOX_TOKEN` through the process environment so the token is not written to the temporary inventory source.

The controller executing inventory refreshes therefore requires the `netbox.netbox` collection (for example `ansible-galaxy collection install netbox.netbox`). Status, tag, site and role remain available as Journeyman query filters. Rich plugin hostvars are enabled by default for interfaces, services, config context, site data and virtual disks; these may be disabled individually when inventory size or NetBox API load warrants it.

Journeyman retains the complete canonical `ansible-inventory --list` result. This makes the upstream plugin's hostvars and groups available to Filtered and Composite Inventories and to Package inputs that consume host-variable data.

Filtered Inventory **Host variable path** fields provide an editable browser-native autocomplete populated from paths observed in the selected source inventory's currently resolved cache. Coverage is shown as `N / total hosts`. The suggestions are advisory: manually entered paths remain valid because compound inventories may be heterogeneous and cached data may not yet contain every legitimate path.

## Red Hat Lightspeed / Insights

Create a URL / API credential using OAuth 2 client credentials. The Inventory reads `/api/inventory/v1/hosts` and supports the Host Inventory API `tags` filter. The system FQDN is preferred as the canonical hostname, with display name used as a fallback. Returned provider data is available beneath `redhat_lightspeed`.

The service account must have permission to read inventory hosts. OAuth access tokens are obtained at refresh time and are not stored in the Journeyman database.


## oVirt / Red Hat Virtualization

The `ovirt` inventory type delegates VM discovery to the upstream
`ovirt.ovirt.ovirt` Ansible inventory plugin. The selected URL / API
credential must use Basic authentication and should point at the Engine API,
for example `https://engine.example.org/ovirt-engine/api`. Journeyman passes
the URL, username, and password through `OVIRT_URL`, `OVIRT_USERNAME`, and
`OVIRT_PASSWORD`; secrets are not written to the temporary inventory source.

The controller executing inventory refreshes requires the `ovirt.ovirt`
collection and `ovirt-engine-sdk-python >= 4.2.4`. Optional query-filter and
hostname-preference settings are passed to the upstream plugin. The complete
`ansible-inventory --list` result is cached, so plugin-provided host variables
and groups remain available to Filtered and Composite Inventories.

## Zabbix provider backend

Journeyman resolves Zabbix inventories through the upstream
`community.zabbix.zabbix_inventory` Ansible inventory plugin rather than
maintaining a separate Zabbix API implementation. The complete `zbx_*`
hostvars emitted by the collection are retained.

Journeyman adds a small derived `zabbix` namespace for automation-friendly
facts used by filtered inventories and Package inputs. In particular:

- `zabbix.icmp.reachable` exposes the current `icmpping` item state.
- `zabbix.network_interfaces` reconstructs discovered `net.if.*` interface
  facts and preserves interface `name` and `alias`, allowing a dependent
  Package Choice input to present switch ports for the selected device.

The provider requests host groups, monitoring interfaces, host inventory,
tags, linked templates and item fields required for ICMP/interface enrichment.
Raw values remain available under their upstream `zbx_*` names.

The Ansible controller used for inventory refresh must have the
`community.zabbix` collection installed and a supported Python version. The
Zabbix API token is passed through an environment lookup and is not written to
the temporary inventory source.
