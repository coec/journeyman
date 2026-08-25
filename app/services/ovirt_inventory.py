"""Resolve oVirt / Red Hat Virtualization inventories through Ansible.

Journeyman deliberately delegates oVirt inventory discovery to the upstream
``ovirt.ovirt.ovirt`` inventory plugin. Credentials are supplied through the
plugin's documented OVIRT_* environment variables so secrets are never
written to the temporary inventory source.
"""

import json
import os
import subprocess
import tempfile

import yaml

from app.services.url_credentials import URLCredentialError, url_credential_details

ANSIBLE_INVENTORY_COMMAND = "/usr/bin/ansible-inventory"


class OvirtInventoryError(Exception):
    """Raised when an oVirt / RHV inventory cannot be resolved."""


def resolve_ovirt_inventory(*, credential, verify_tls=True, query_filter=None,
                            hostname_preference=None, proxy_url=None, timeout=120):
    try:
        username, data = url_credential_details(credential)
    except URLCredentialError as exc:
        raise OvirtInventoryError(str(exc)) from exc

    if data.get("auth_mode") != "basic":
        raise OvirtInventoryError(
            "oVirt / RHV inventory requires a URL / API credential using Basic authentication."
        )

    password = str(data.get("password") or "")
    if not username or not password:
        raise OvirtInventoryError("oVirt / RHV username and password are required.")

    source = {
        "plugin": "ovirt.ovirt.ovirt",
        "ovirt_insecure": not bool(verify_tls),
        "ovirt_hostname_preference": hostname_preference or ["fqdn", "name"],
    }
    if query_filter:
        if not isinstance(query_filter, dict):
            raise OvirtInventoryError("oVirt / RHV query filter must be a mapping.")
        source["ovirt_query_filter"] = query_filter

    inventory_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="journeyman-ovirt-",
            suffix=".ovirt.yml", delete=False,
        ) as handle:
            inventory_path = handle.name
            os.chmod(inventory_path, 0o600)
            yaml.safe_dump(source, handle, default_flow_style=False, sort_keys=False)

        env = os.environ.copy()
        env.update({
            "OVIRT_URL": data["url"],
            "OVIRT_USERNAME": username,
            "OVIRT_PASSWORD": password,
        })
        if proxy_url:
            env["HTTPS_PROXY"] = proxy_url
            env["https_proxy"] = proxy_url

        result = subprocess.run(
            [ANSIBLE_INVENTORY_COMMAND, "--inventory", inventory_path, "--list"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False, env=env,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "ansible-inventory failed."
            message = message.replace(inventory_path, "<temporary oVirt inventory>")
            # Do not let subprocess diagnostics echo credentials back to the UI/logs.
            for secret in (password, data.get("url"), username):
                if secret:
                    message = message.replace(str(secret), "<redacted>")
            raise OvirtInventoryError("Unable to resolve oVirt / RHV inventory: " + message)

        try:
            inventory_data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OvirtInventoryError("ansible-inventory returned invalid JSON for oVirt / RHV.") from exc
        if not isinstance(inventory_data, dict):
            raise OvirtInventoryError("oVirt / RHV inventory produced an invalid result.")
        hostvars = inventory_data.get("_meta", {}).get("hostvars", {})
        if not isinstance(hostvars, dict):
            raise OvirtInventoryError("oVirt / RHV inventory produced no hostvars mapping.")
        return inventory_data
    except subprocess.TimeoutExpired as exc:
        raise OvirtInventoryError("oVirt / RHV inventory resolution timed out.") from exc
    except OSError as exc:
        raise OvirtInventoryError("Unable to execute ansible-inventory: " + str(exc)) from exc
    finally:
        if inventory_path:
            try:
                os.remove(inventory_path)
            except OSError:
                pass
