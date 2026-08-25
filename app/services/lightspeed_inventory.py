"""Resolve Red Hat Lightspeed (Insights) Host Inventory into Ansible inventory."""

from urllib.parse import urlencode

from app.services.http_json import HTTPJSONError, get_json


class LightspeedInventoryError(RuntimeError):
    pass


def _host_identity(host):
    return str(host.get("fqdn") or host.get("display_name") or "").strip()


def _record_identifier(host):
    for key in (
        "id",
        "insights_id",
        "subscription_manager_id",
        "bios_uuid",
    ):
        value = str(host.get(key) or "").strip()
        if value:
            return value
    return ""


def resolve_lightspeed_inventory(*, credential, verify_tls=True, tags="", timeout=30, proxy_url=None):
    query = [("per_page", "100")]
    if str(tags or "").strip():
        query.append(("tags", str(tags).strip()))
    path = "/api/inventory/v1/hosts?" + urlencode(query)
    inventory = {
        "_meta": {
            "hostvars": {},
            "journeyman_provider_diagnostics": {
                "source_records": 0,
                "resolved_hosts": 0,
                "duplicate_source_records": 0,
                "duplicate_identities": {},
            },
        },
        "all": {"children": ["lightspeed_hosts"]},
        "lightspeed_hosts": {"hosts": []},
    }
    diagnostics = inventory["_meta"]["journeyman_provider_diagnostics"]
    source_records_by_hostname = {}
    page = 1
    try:
        while True:
            page_path = path + "&page={}".format(page)
            payload = get_json(credential, page_path, verify_tls=verify_tls, timeout=timeout, proxy_url=proxy_url)
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise LightspeedInventoryError("Red Hat Lightspeed inventory response has no results list.")

            diagnostics["source_records"] += len(results)

            for host in results:
                if not isinstance(host, dict):
                    continue
                hostname = _host_identity(host)
                if not hostname:
                    continue

                source_records = source_records_by_hostname.setdefault(
                    hostname,
                    [],
                )
                source_records.append(host)

                if hostname in inventory["_meta"]["hostvars"]:
                    # Ansible inventory identity is the hostname, so duplicate
                    # provider records must not silently overwrite the first
                    # record. Preserve every source object for inspection.
                    existing_vars = inventory["_meta"]["hostvars"][hostname]
                    existing_vars["redhat_lightspeed_source_records"] = list(
                        source_records
                    )
                    existing_vars["redhat_lightspeed_duplicate_source_count"] = (
                        len(source_records) - 1
                    )
                    continue

                vars_ = {
                    "ansible_host": hostname,
                    "redhat_lightspeed": host,
                }
                system_profile = host.get("system_profile")
                if isinstance(system_profile, dict):
                    vars_["redhat_lightspeed_system_profile"] = system_profile
                inventory["_meta"]["hostvars"][hostname] = vars_
                inventory["lightspeed_hosts"]["hosts"].append(hostname)

            total = payload.get("total") if isinstance(payload, dict) else None
            response_page = payload.get("page") if isinstance(payload, dict) else None
            response_per_page = payload.get("per_page") if isinstance(payload, dict) else None

            if not results:
                break

            if isinstance(total, int) and total >= 0:
                current_page = response_page if isinstance(response_page, int) and response_page > 0 else page
                page_size = (
                    response_per_page
                    if isinstance(response_per_page, int) and response_per_page > 0
                    else len(results)
                )
                if current_page * page_size >= total:
                    break
            elif isinstance(response_per_page, int) and response_per_page > 0:
                if len(results) < response_per_page:
                    break
            elif len(results) < 100:
                break

            page += 1
    except HTTPJSONError as exc:
        raise LightspeedInventoryError(str(exc)) from exc

    duplicate_identities = {}
    duplicate_source_records = 0
    for hostname, source_records in source_records_by_hostname.items():
        if len(source_records) <= 1:
            continue
        duplicate_count = len(source_records) - 1
        duplicate_source_records += duplicate_count
        duplicate_identities[hostname] = {
            "source_records": len(source_records),
            "duplicate_source_records": duplicate_count,
            "record_ids": [
                identifier
                for identifier in (
                    _record_identifier(host)
                    for host in source_records
                )
                if identifier
            ],
        }

    inventory["lightspeed_hosts"]["hosts"] = sorted(
        set(inventory["lightspeed_hosts"]["hosts"])
    )
    diagnostics["resolved_hosts"] = len(
        inventory["_meta"]["hostvars"]
    )
    diagnostics["duplicate_source_records"] = duplicate_source_records
    diagnostics["duplicate_identities"] = duplicate_identities
    return inventory
