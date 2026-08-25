"""Resolve NetBox inventories through the upstream Ansible inventory plugin.

Journeyman delegates NetBox object modelling to ``netbox.netbox.nb_inventory``
so the complete hostvars produced by the collection (interfaces, services,
config context, site data, custom fields, etc.) remain available to filtered,
composite and Package-driven workflows.
"""

import json
import os
import subprocess
import tempfile

import yaml

from app.services.url_credentials import URLCredentialError, url_credential_details

ANSIBLE_INVENTORY_COMMAND = "/usr/bin/ansible-inventory"


class NetBoxInventoryError(RuntimeError):
    pass


def _query_filters(*, status="active", tag="", site="", role=""):
    result = []
    for key, value in (("status", status), ("tag", tag), ("site", site), ("role", role)):
        value = str(value or "").strip()
        if value:
            result.append({key: value})
    return result


def resolve_netbox_inventory(
    *, credential, verify_tls=True, status="active", tag="", site="", role="",
    interfaces=True, services=True, config_context=True, site_data=True,
    virtual_disks=True, timeout=120, proxy_url=None,
):
    try:
        _username, data = url_credential_details(credential)
    except URLCredentialError as exc:
        raise NetBoxInventoryError(str(exc)) from exc

    if data.get("auth_mode") != "token":
        raise NetBoxInventoryError(
            "NetBox inventory requires a URL / API credential using Token authentication."
        )

    token = str(data.get("token") or "")
    if not token:
        raise NetBoxInventoryError("NetBox API token is required.")

    source = {
        "plugin": "netbox.netbox.nb_inventory",
        "validate_certs": bool(verify_tls),
        "interfaces": bool(interfaces),
        "services": bool(services),
        "config_context": bool(config_context),
        "site_data": bool(site_data),
        "virtual_disks": bool(virtual_disks),
    }
    filters = _query_filters(status=status, tag=tag, site=site, role=role)
    if filters:
        source["query_filters"] = filters

    inventory_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="journeyman-netbox-",
            suffix=".netbox.yml", delete=False,
        ) as handle:
            inventory_path = handle.name
            os.chmod(inventory_path, 0o600)
            yaml.safe_dump(source, handle, default_flow_style=False, sort_keys=False)

        env = os.environ.copy()
        env.update({
            "NETBOX_API": data["url"],
            "NETBOX_TOKEN": token,
        })
        if proxy_url:
            env["HTTPS_PROXY"] = proxy_url
            env["https_proxy"] = proxy_url

        result = subprocess.run(
            [ANSIBLE_INVENTORY_COMMAND, "--inventory", inventory_path, "--list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "ansible-inventory failed."
            message = message.replace(inventory_path, "<temporary NetBox inventory>")
            for secret in (token, data.get("url")):
                if secret:
                    message = message.replace(str(secret), "<redacted>")
            raise NetBoxInventoryError("Unable to resolve NetBox inventory: " + message)

        try:
            inventory_data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise NetBoxInventoryError(
                "ansible-inventory returned invalid JSON for NetBox."
            ) from exc
        if not isinstance(inventory_data, dict):
            raise NetBoxInventoryError("NetBox inventory produced an invalid result.")
        hostvars = inventory_data.get("_meta", {}).get("hostvars", {})
        if not isinstance(hostvars, dict):
            raise NetBoxInventoryError("NetBox inventory produced no hostvars mapping.")
        return inventory_data
    except subprocess.TimeoutExpired as exc:
        raise NetBoxInventoryError("NetBox inventory resolution timed out.") from exc
    except OSError as exc:
        raise NetBoxInventoryError("Unable to execute ansible-inventory: " + str(exc)) from exc
    finally:
        if inventory_path:
            try:
                os.remove(inventory_path)
            except OSError:
                pass
