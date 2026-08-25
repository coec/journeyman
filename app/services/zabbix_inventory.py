"""
Resolve Zabbix hosts into canonical Ansible inventory JSON.

Authentication uses a Zabbix API token sent through the HTTP
Authorization Bearer header. Usernames and passwords are not used.
"""

import json
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from app.services.outbound_security import (
    OutboundSecurityError, secure_transport_enforced, validate_outbound_url,
)


class ZabbixInventoryError(Exception):
    """
    Raised when a Zabbix inventory cannot be resolved.
    """


def _normalise_api_url(endpoint):
    """
    Accept either a Zabbix base URL or an API endpoint.
    """

    endpoint = str(
        endpoint or ""
    ).strip().rstrip("/")

    if not endpoint:
        raise ZabbixInventoryError(
            "Zabbix API URL is required."
        )

    parsed = urlparse(
        endpoint
    )

    if parsed.scheme != "https":
        raise ZabbixInventoryError("Zabbix API URL must use https://.")

    if not parsed.netloc:
        raise ZabbixInventoryError(
            "Zabbix API URL has no hostname."
        )

    if endpoint.endswith(
        "/api_jsonrpc.php"
    ):
        return endpoint

    return "{}/api_jsonrpc.php".format(
        endpoint
    )


def _ssl_context(verify_tls=True):
    if not verify_tls and secure_transport_enforced():
        raise ZabbixInventoryError("Zabbix TLS certificate verification cannot be disabled.")
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ZabbixInventoryError("Zabbix API redirects are not permitted.")

def _request_hosts(
    *,
    endpoint,
    token,
    tag_name,
    tag_value,
    verify_tls,
    include_disabled,
    timeout,
    proxy_url=None,
):
    """
    Execute one Zabbix host.get API request.
    """

    try:
        endpoint = validate_outbound_url(endpoint, purpose="Zabbix API")
    except OutboundSecurityError as exc:
        raise ZabbixInventoryError(str(exc)) from exc
    api_url = _normalise_api_url(endpoint)

    token = str(
        token or ""
    ).strip()

    if not token:
        raise ZabbixInventoryError(
            "Zabbix API token is missing."
        )

    tag_name = str(
        tag_name or ""
    ).strip()

    tag_value = str(
        tag_value or ""
    ).strip()

    if not tag_name:
        raise ZabbixInventoryError(
            "Zabbix host tag name is required."
        )

    if not tag_value:
        raise ZabbixInventoryError(
            "Zabbix host tag value is required."
        )

    params = {
        "output": [
            "hostid",
            "host",
            "name",
            "status",
            "description",
            "inventory_mode",
            "monitored_by",
            "proxyid",
            "assigned_proxyid",
        ],
        "selectInterfaces": [
            "interfaceid",
            "main",
            "type",
            "useip",
            "ip",
            "dns",
            "port",
            "available",
            "error",
        ],
        "selectHostGroups": [
            "groupid",
            "name",
        ],
        "selectTags": "extend",
        "selectInventory": "extend",
        "selectParentTemplates": [
            "templateid",
            "host",
            "name",
        ],
        "evaltype": 0,
        "tags": [
            {
                "tag": tag_name,
                "value": tag_value,
                "operator": 1,
            }
        ],
        "sortfield": "host",
    }

    if not include_disabled:
        params["filter"] = {
            "status": "0",
        }

    payload = {
        "jsonrpc": "2.0",
        "method": "host.get",
        "params": params,
        "id": 1,
    }

    request = Request(
        api_url,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Authorization": (
                "Bearer {}".format(token)
            ),
            "Content-Type": (
                "application/json-rpc"
            ),
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        context = _ssl_context(verify_tls)
        # Never follow API redirects. An allowed endpoint must not be able to
        # redirect Journeyman to a different destination or transport.
        handlers = [HTTPSHandler(context=context), _NoRedirect()]
        if proxy_url:
            handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = build_opener(*handlers)
        response_context = opener.open(request, timeout=timeout)
        with response_context as response:
            response_data = response.read()

    except HTTPError as exc:
        raise ZabbixInventoryError(
            "Zabbix API returned HTTP {}."
            .format(exc.code)
        ) from exc

    except (
        URLError,
        socket.timeout,
        TimeoutError,
        ssl.SSLError,
    ) as exc:
        raise ZabbixInventoryError(
            "Unable to connect to the Zabbix API: {}"
            .format(exc)
        ) from exc

    try:
        result = json.loads(
            response_data.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ZabbixInventoryError(
            "Zabbix API returned invalid JSON."
        ) from exc

    if not isinstance(result, dict):
        raise ZabbixInventoryError(
            "Zabbix API returned an invalid response."
        )

    api_error = result.get(
        "error"
    )

    if api_error:
        if isinstance(api_error, dict):
            error_code = api_error.get(
                "code",
                "unknown",
            )

            error_message = api_error.get(
                "message",
                "Unknown API error",
            )

            error_data = str(
                api_error.get("data") or ""
            ).strip()
        else:
            error_code = "unknown"
            error_message = str(
                api_error
            )
            error_data = ""

        detail = (
            " {}".format(error_data)
            if error_data
            else ""
        )

        raise ZabbixInventoryError(
            "Zabbix API error {}: {}.{}"
            .format(
                error_code,
                error_message,
                detail,
            )
        )

    hosts = result.get(
        "result"
    )

    if not isinstance(hosts, list):
        raise ZabbixInventoryError(
            "Zabbix API response contains no host list."
        )

    return hosts


def _request_icmp_items(
    *,
    endpoint,
    token,
    hostids,
    verify_tls,
    timeout,
    proxy_url=None,
):
    """
    Retrieve ICMP ping items for the selected Zabbix hosts.

    ``item.get`` is deliberately restricted to keys beginning with
    ``icmpping``. Pulling every item attached to every host would turn an
    inventory refresh into a latest-data export and could be extremely large.
    """

    hostids = sorted({
        str(hostid or "").strip()
        for hostid in (hostids or [])
        if str(hostid or "").strip()
    })
    if not hostids:
        return []

    try:
        endpoint = validate_outbound_url(endpoint, purpose="Zabbix API")
    except OutboundSecurityError as exc:
        raise ZabbixInventoryError(str(exc)) from exc
    api_url = _normalise_api_url(endpoint)

    token = str(token or "").strip()
    if not token:
        raise ZabbixInventoryError("Zabbix API token is missing.")

    payload = {
        "jsonrpc": "2.0",
        "method": "item.get",
        "params": {
            "output": [
                "itemid",
                "hostid",
                "name",
                "key_",
                "status",
                "state",
                "error",
                "lastvalue",
                "lastclock",
                "units",
                "value_type",
            ],
            "hostids": hostids,
            "search": {
                "key_": "icmpping",
            },
            "startSearch": True,
            "sortfield": [
                "name",
                "key_",
            ],
        },
        "id": 2,
    }

    request = Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json-rpc",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        context = _ssl_context(verify_tls)
        handlers = [HTTPSHandler(context=context), _NoRedirect()]
        if proxy_url:
            handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = build_opener(*handlers)
        response_context = opener.open(request, timeout=timeout)
        with response_context as response:
            response_data = response.read()
    except HTTPError as exc:
        raise ZabbixInventoryError(
            "Zabbix API returned HTTP {}.".format(exc.code)
        ) from exc
    except (URLError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
        raise ZabbixInventoryError(
            "Unable to connect to the Zabbix API: {}".format(exc)
        ) from exc

    try:
        result = json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ZabbixInventoryError("Zabbix API returned invalid JSON.") from exc

    if not isinstance(result, dict):
        raise ZabbixInventoryError("Zabbix API returned an invalid response.")
    api_error = result.get("error")
    if api_error:
        if isinstance(api_error, dict):
            error_code = api_error.get("code", "unknown")
            error_message = api_error.get("message", "Unknown API error")
            error_data = str(api_error.get("data") or "").strip()
        else:
            error_code = "unknown"
            error_message = str(api_error)
            error_data = ""
        detail = " {}".format(error_data) if error_data else ""
        raise ZabbixInventoryError(
            "Zabbix API error {}: {}.{}".format(
                error_code, error_message, detail
            )
        )

    items = result.get("result")
    if not isinstance(items, list):
        raise ZabbixInventoryError(
            "Zabbix API response contains no ICMP item list."
        )
    return items



def _request_network_interface_items(
    *,
    endpoint,
    token,
    hostids,
    verify_tls,
    timeout,
    proxy_url=None,
):
    """Retrieve interface-related items for selected Zabbix hosts.

    The query is deliberately restricted to ``net.if.`` item keys. This
    captures interface LLD facts and metrics from standard SNMP templates
    without importing every monitored item on the host.
    """

    hostids = sorted({
        str(hostid or "").strip()
        for hostid in (hostids or [])
        if str(hostid or "").strip()
    })
    if not hostids:
        return []

    try:
        endpoint = validate_outbound_url(endpoint, purpose="Zabbix API")
    except OutboundSecurityError as exc:
        raise ZabbixInventoryError(str(exc)) from exc
    api_url = _normalise_api_url(endpoint)

    token = str(token or "").strip()
    if not token:
        raise ZabbixInventoryError("Zabbix API token is missing.")

    payload = {
        "jsonrpc": "2.0",
        "method": "item.get",
        "params": {
            "output": [
                "itemid",
                "hostid",
                "name",
                "key_",
                "status",
                "state",
                "error",
                "lastvalue",
                "lastclock",
                "units",
                "value_type",
            ],
            "hostids": hostids,
            "search": {"key_": "net.if."},
            "startSearch": True,
            "sortfield": ["name", "key_"],
        },
        "id": 3,
    }

    request = Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json-rpc",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        context = _ssl_context(verify_tls)
        handlers = [HTTPSHandler(context=context), _NoRedirect()]
        if proxy_url:
            handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = build_opener(*handlers)
        response_context = opener.open(request, timeout=timeout)
        with response_context as response:
            response_data = response.read()
    except HTTPError as exc:
        raise ZabbixInventoryError(
            "Zabbix API returned HTTP {}.".format(exc.code)
        ) from exc
    except (URLError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
        raise ZabbixInventoryError(
            "Unable to connect to the Zabbix API: {}".format(exc)
        ) from exc

    try:
        result = json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ZabbixInventoryError("Zabbix API returned invalid JSON.") from exc

    if not isinstance(result, dict):
        raise ZabbixInventoryError("Zabbix API returned an invalid response.")
    api_error = result.get("error")
    if api_error:
        if isinstance(api_error, dict):
            error_code = api_error.get("code", "unknown")
            error_message = api_error.get("message", "Unknown API error")
            error_data = str(api_error.get("data") or "").strip()
        else:
            error_code = "unknown"
            error_message = str(api_error)
            error_data = ""
        detail = " {}".format(error_data) if error_data else ""
        raise ZabbixInventoryError(
            "Zabbix API error {}: {}.{}".format(
                error_code, error_message, detail
            )
        )

    items = result.get("result")
    if not isinstance(items, list):
        raise ZabbixInventoryError(
            "Zabbix API response contains no network-interface item list."
        )
    return items


def _item_interface_index(key):
    """Return the discovered interface identity encoded in a net.if key."""

    key = str(key or "").strip()
    if not key.startswith("net.if.") or "[" not in key or not key.endswith("]"):
        return ""
    argument = key[key.find("[") + 1:-1].split(",", 1)[0].strip()
    if not argument:
        return ""
    # Standard SNMP interface templates use arguments such as
    # ifName.10101, ifAlias.10101 and ifOperStatus.10101.
    if "." in argument:
        return argument.rsplit(".", 1)[1].strip()
    return argument


def _normalise_network_interfaces(items):
    """Build structured interface facts from standard ``net.if.*`` items."""

    by_index = {}
    field_by_prefix = {
        "net.if.name": "name",
        "net.if.alias": "alias",
        "net.if.descr": "description",
        "net.if.status": "oper_status",
        "net.if.adminstatus": "admin_status",
        "net.if.speed": "speed",
        "net.if.type": "type",
        "net.if.mtu": "mtu",
    }

    for item in (items or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key_", "") or "").strip()
        index = _item_interface_index(key)
        if not index:
            continue
        row = by_index.setdefault(index, {
            "index": index,
            "name": "",
            "alias": "",
            "description": "",
            "oper_status": "",
            "admin_status": "",
            "speed": "",
            "type": "",
            "mtu": "",
            "items": {},
        })
        normalised_item = {
            "itemid": str(item.get("itemid", "") or ""),
            "name": str(item.get("name", "") or ""),
            "key": key,
            "enabled": str(item.get("status", "1")) == "0",
            "supported": str(item.get("state", "1")) == "0",
            "error": str(item.get("error", "") or ""),
            "last_value": str(item.get("lastvalue", "") or ""),
            "last_clock": str(item.get("lastclock", "") or ""),
            "units": str(item.get("units", "") or ""),
            "value_type": str(item.get("value_type", "") or ""),
        }
        row["items"][key] = normalised_item

        item_name = normalised_item["name"]
        if not row["name"] and item_name:
            match = re.match(r"^Interface\s+(.+?)\((.*?)\):", item_name)
            if match:
                row["name"] = match.group(1).strip()
                if not row["alias"]:
                    row["alias"] = match.group(2).strip()
            else:
                match = re.match(r"^Interface\s+\[([^]]+)\]\[([^]]*)\]:", item_name)
                if match:
                    row["name"] = match.group(1).strip()
                    if not row["alias"]:
                        row["alias"] = match.group(2).strip()

        prefix = key.split("[", 1)[0]
        field = field_by_prefix.get(prefix)
        if field and normalised_item["last_value"]:
            row[field] = normalised_item["last_value"]

    return sorted(
        by_index.values(),
        key=lambda row: (row.get("name") or row.get("index"), row.get("index")),
    )

def _normalise_templates(value):
    """Return linked template identity data without host macros or secrets."""

    if not isinstance(value, list):
        return []

    templates = []
    for template in value:
        if not isinstance(template, dict):
            continue
        templateid = str(template.get("templateid", "") or "").strip()
        if not templateid:
            continue
        templates.append({
            "templateid": templateid,
            "host": str(template.get("host", "") or ""),
            "name": str(template.get("name", "") or ""),
        })
    return templates


def _normalise_icmp_item(value):
    """Return filter-friendly state for one Zabbix ICMP item."""

    if not isinstance(value, dict):
        return None

    key = str(value.get("key_", "") or "").strip()
    if not key.startswith("icmpping"):
        return None

    last_value = str(value.get("lastvalue", "") or "").strip()
    enabled = str(value.get("status", "1")) == "0"
    supported = str(value.get("state", "1")) == "0"
    reachable = None
    if enabled and supported and last_value in {"0", "1"}:
        reachable = last_value == "1"

    return {
        "itemid": str(value.get("itemid", "") or ""),
        "name": str(value.get("name", "") or ""),
        "key": key,
        "enabled": enabled,
        "supported": supported,
        "error": str(value.get("error", "") or ""),
        "last_value": last_value,
        "last_clock": str(value.get("lastclock", "") or ""),
        "units": str(value.get("units", "") or ""),
        "value_type": str(value.get("value_type", "") or ""),
        "reachable": reachable,
    }


def _normalise_interfaces(value):
    """
    Return safe interface data from one Zabbix host.
    """

    if not isinstance(value, list):
        return []

    interfaces = []

    for interface in value:
        if not isinstance(interface, dict):
            continue

        interfaces.append(
            {
                "interfaceid": str(
                    interface.get(
                        "interfaceid",
                        "",
                    )
                ),
                "main": (
                    str(
                        interface.get(
                            "main",
                            "0",
                        )
                    )
                    == "1"
                ),
                "type": str(
                    interface.get(
                        "type",
                        "",
                    )
                ),
                "use_ip": (
                    str(
                        interface.get(
                            "useip",
                            "0",
                        )
                    )
                    == "1"
                ),
                "ip": str(
                    interface.get(
                        "ip",
                        "",
                    )
                    or ""
                ),
                "dns": str(
                    interface.get(
                        "dns",
                        "",
                    )
                    or ""
                ),
                "port": str(
                    interface.get(
                        "port",
                        "",
                    )
                    or ""
                ),
                "available": str(
                    interface.get(
                        "available",
                        "",
                    )
                    or ""
                ),
                "error": str(
                    interface.get(
                        "error",
                        "",
                    )
                    or ""
                ),
            }
        )

    return interfaces


def _normalise_groups(value):
    """
    Return safe group data from one Zabbix host.
    """

    if not isinstance(value, list):
        return []

    groups = []

    for group in value:
        if not isinstance(group, dict):
            continue

        group_id = str(
            group.get(
                "groupid",
                "",
            )
        ).strip()

        group_name = str(
            group.get(
                "name",
                "",
            )
            or ""
        ).strip()

        if not group_id:
            continue

        groups.append(
            {
                "groupid": group_id,
                "name": group_name,
            }
        )

    return groups


def _normalise_tags(value):
    """
    Return tag records and a convenient tag-name mapping.
    """

    if not isinstance(value, list):
        return [], {}

    tags = []
    values_by_name = {}

    for tag in value:
        if not isinstance(tag, dict):
            continue

        tag_name = str(
            tag.get(
                "tag",
                "",
            )
            or ""
        ).strip()

        tag_value = str(
            tag.get(
                "value",
                "",
            )
            or ""
        )

        if not tag_name:
            continue

        tags.append(
            {
                "tag": tag_name,
                "value": tag_value,
            }
        )

        values_by_name.setdefault(
            tag_name,
            [],
        )

        if (
            tag_value
            not in values_by_name[tag_name]
        ):
            values_by_name[
                tag_name
            ].append(
                tag_value
            )

    return tags, values_by_name


def _group_key(group_id):
    """
    Build a unique Ansible-safe group name.
    """

    return "zabbix_group_{}".format(
        group_id
    )


def _canonical_inventory(hosts, icmp_items=None, network_interface_items=None):
    """
    Convert Zabbix host objects to canonical Ansible inventory JSON.
    """

    inventory = {
        "_meta": {
            "hostvars": {},
        },
        "all": {
            "children": [
                "zabbix_hosts",
            ],
        },
        "zabbix_hosts": {
            "hosts": [],
        },
    }

    group_names = set()

    icmp_by_hostid = {}
    for item in (icmp_items or []):
        normalised = _normalise_icmp_item(item)
        if normalised is None:
            continue
        hostid = str(item.get("hostid", "") or "").strip()
        if not hostid:
            continue
        icmp_by_hostid.setdefault(hostid, []).append(normalised)

    network_items_by_hostid = {}
    for item in (network_interface_items or []):
        if not isinstance(item, dict):
            continue
        hostid = str(item.get("hostid", "") or "").strip()
        if hostid:
            network_items_by_hostid.setdefault(hostid, []).append(item)

    for host in hosts:
        if not isinstance(host, dict):
            continue

        hostname = str(
            host.get(
                "host",
                "",
            )
            or ""
        ).strip()

        if not hostname:
            continue

        interfaces = _normalise_interfaces(
            host.get(
                "interfaces"
            )
        )

        groups = _normalise_groups(
            host.get(
                "hostgroups"
            )
        )

        tags, tags_by_name = (
            _normalise_tags(
                host.get(
                    "tags"
                )
            )
        )

        templates = _normalise_templates(
            host.get("parentTemplates")
        )

        host_inventory = host.get(
            "inventory"
        )

        if not isinstance(
            host_inventory,
            dict,
        ):
            host_inventory = {}

        hostvars = {
            "zabbix": {
                "hostid": str(
                    host.get(
                        "hostid",
                        "",
                    )
                ),
                "visible_name": str(
                    host.get(
                        "name",
                        "",
                    )
                    or ""
                ),
                "enabled": (
                    str(
                        host.get(
                            "status",
                            "1",
                        )
                    )
                    == "0"
                ),
                "description": str(
                    host.get(
                        "description",
                        "",
                    )
                    or ""
                ),
                "inventory_mode": str(
                    host.get(
                        "inventory_mode",
                        "",
                    )
                    or ""
                ),
                "monitored_by": str(
                    host.get(
                        "monitored_by",
                        "",
                    )
                    or ""
                ),
                "proxyid": str(
                    host.get(
                        "proxyid",
                        "",
                    )
                    or ""
                ),
                "assigned_proxyid": str(
                    host.get(
                        "assigned_proxyid",
                        "",
                    )
                    or ""
                ),
                "interfaces": interfaces,
                "hostgroups": groups,
                "tags": tags,
                "tags_by_name": tags_by_name,
                "inventory": host_inventory,
                "templates": templates,
                "network_interfaces": _normalise_network_interfaces(
                    network_items_by_hostid.get(str(host.get("hostid", "") or ""), [])
                ),
                "icmp": {
                    "items": icmp_by_hostid.get(
                        str(host.get("hostid", "") or ""),
                        [],
                    ),
                },
            },
        }

        icmp_items_for_host = hostvars["zabbix"]["icmp"]["items"]
        primary_icmp = next(
            (item for item in icmp_items_for_host if item["key"] == "icmpping"),
            icmp_items_for_host[0] if icmp_items_for_host else None,
        )
        if primary_icmp is not None:
            hostvars["zabbix"]["icmp"].update({
                "configured": True,
                "reachable": primary_icmp["reachable"],
                "last_value": primary_icmp["last_value"],
                "last_clock": primary_icmp["last_clock"],
                "itemid": primary_icmp["itemid"],
                "key": primary_icmp["key"],
            })
        else:
            hostvars["zabbix"]["icmp"].update({
                "configured": False,
                "reachable": None,
                "last_value": "",
                "last_clock": "",
                "itemid": "",
                "key": "",
            })

        # The Zabbix host field is the canonical managed-host identity.
        # Do not replace it with an interface address when Zabbix has
        # ``useip=1``: doing so makes the same machine resolve under a
        # different identity from Satellite/static inventories and bypasses
        # normal hostname-based matching.  Interface IP/DNS details remain
        # available under ``zabbix.interfaces`` for filtering/inspection.
        hostvars["ansible_host"] = hostname

        runner_values = tags_by_name.get(
            "journeyman_runner",
            [],
        )
        if len(runner_values) == 1:
            hostvars["journeyman_runner"] = (
                runner_values[0]
            )

        inventory[
            "_meta"
        ][
            "hostvars"
        ][hostname] = hostvars

        inventory[
            "zabbix_hosts"
        ][
            "hosts"
        ].append(
            hostname
        )

        for group in groups:
            group_name = _group_key(
                group["groupid"]
            )

            group_names.add(
                group_name
            )

            inventory.setdefault(
                group_name,
                {
                    "hosts": [],
                    "vars": {
                        "zabbix_group_id": (
                            group["groupid"]
                        ),
                        "zabbix_group_name": (
                            group["name"]
                        ),
                    },
                },
            )

            inventory[
                group_name
            ][
                "hosts"
            ].append(
                hostname
            )

    inventory[
        "zabbix_hosts"
    ][
        "hosts"
    ] = sorted(
        set(
            inventory[
                "zabbix_hosts"
            ][
                "hosts"
            ]
        )
    )

    for group_name in sorted(
        group_names
    ):
        inventory[
            group_name
        ][
            "hosts"
        ] = sorted(
            set(
                inventory[
                    group_name
                ][
                    "hosts"
                ]
            )
        )

        inventory[
            "all"
        ][
            "children"
        ].append(
            group_name
        )

    return inventory


def resolve_zabbix_inventory(
    *,
    endpoint,
    token,
    tag_name,
    tag_value,
    verify_tls=True,
    include_disabled=False,
    timeout=30,
    proxy_url=None,
):
    """
    Query Zabbix and return canonical Ansible inventory JSON.
    """

    hosts = _request_hosts(
        endpoint=endpoint,
        token=token,
        tag_name=tag_name,
        tag_value=tag_value,
        verify_tls=verify_tls,
        include_disabled=include_disabled,
        timeout=timeout,
        proxy_url=proxy_url,
    )

    icmp_items = _request_icmp_items(
        endpoint=endpoint,
        token=token,
        hostids=[host.get("hostid") for host in hosts if isinstance(host, dict)],
        verify_tls=verify_tls,
        timeout=timeout,
        proxy_url=proxy_url,
    )

    network_interface_items = _request_network_interface_items(
        endpoint=endpoint,
        token=token,
        hostids=[host.get("hostid") for host in hosts if isinstance(host, dict)],
        verify_tls=verify_tls,
        timeout=timeout,
        proxy_url=proxy_url,
    )

    return _canonical_inventory(
        hosts,
        icmp_items=icmp_items,
        network_interface_items=network_interface_items,
    )
