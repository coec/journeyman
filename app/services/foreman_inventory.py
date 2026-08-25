"""
Red Hat Satellite inventory provider.

Journeyman resolves Satellite inventories using the
theforeman.foreman.foreman Ansible inventory plugin. This produces
the same native Ansible inventory structure used by AAP when supplied
with equivalent source variables.
"""

import json
import os
import shutil
import subprocess
import tempfile

import yaml


ANSIBLE_INVENTORY_COMMAND = "/usr/bin/ansible-inventory"

SATELLITE_PLUGIN = "theforeman.foreman.foreman"

DEFAULT_TIMEOUT_SECONDS = 300

PREVIEW_HOST_LIMIT = 100

_NO_REDIRECT_SITECUSTOMIZE = r"""
from requests.sessions import Session

_journeyman_original_request = Session.request


def _journeyman_request_without_redirects(self, method, url, **kwargs):
    kwargs["allow_redirects"] = False
    response = _journeyman_original_request(
        self,
        method,
        url,
        **kwargs
    )
    if 300 <= int(getattr(response, "status_code", 0)) < 400:
        raise RuntimeError(
            "Journeyman blocked an outbound HTTP redirect."
        )
    return response


Session.request = _journeyman_request_without_redirects
"""


def _install_no_redirect_guard(environment):
    """Install a private sitecustomize for the Foreman subprocess.

    The upstream Foreman inventory plugin uses requests.Session.get without
    disabling redirects. Python imports sitecustomize from PYTHONPATH during
    interpreter startup, so this guard applies only to the ansible-inventory
    subprocess and forces every requests Session request to fail on 3xx.
    """

    guard_dir = tempfile.mkdtemp(
        prefix="journeyman-foreman-http-",
    )
    os.chmod(guard_dir, 0o700)

    guard_path = os.path.join(
        guard_dir,
        "sitecustomize.py",
    )
    with open(
        guard_path,
        "w",
        encoding="utf-8",
    ) as guard_file:
        guard_file.write(
            _NO_REDIRECT_SITECUSTOMIZE
        )
    os.chmod(guard_path, 0o600)

    existing_pythonpath = str(
        environment.get(
            "PYTHONPATH",
            "",
        )
        or ""
    ).strip()

    environment["PYTHONPATH"] = (
        guard_dir
        if not existing_pythonpath
        else "{}{}{}".format(
            guard_dir,
            os.pathsep,
            existing_pythonpath,
        )
    )

    return guard_dir


from app.services.outbound_security import (
    OutboundSecurityError, secure_transport_enforced, validate_outbound_url,
)


class ForemanInventoryError(Exception):
    """
    Raised when Satellite inventory resolution fails.
    """


def _satellite_host_filter(organization):
    """
    Build the Satellite organization filter safely.
    """

    organization = str(
        organization or ""
    ).strip()

    escaped = (
        organization
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )

    return 'organization="{}"'.format(
        escaped
    )


def _build_plugin_config(
    *,
    host,
    username,
    password,
    organization,
    verify_tls,
    proxy_url=None,
):
    """
    Build the Foreman inventory-plugin configuration.

    This configuration mirrors the rich AAP-style inventory tested
    manually with ansible-inventory.
    """

    host = str(host or "").strip().rstrip("/")
    username = str(username or "").strip()
    organization = str(
        organization or ""
    ).strip()

    if not host:
        raise ForemanInventoryError(
            "The Satellite credential has no URL."
        )

    if not username:
        raise ForemanInventoryError(
            "The Satellite credential has no username."
        )

    if not password:
        raise ForemanInventoryError(
            "The Satellite credential has no password."
        )

    if not organization:
        raise ForemanInventoryError(
            "The inventory has no Satellite organization."
        )

    return {
        "plugin": SATELLITE_PLUGIN,
        "url": host,
        "user": username,
        "password": password,
        "validate_certs": bool(verify_tls),

        "host_filters": _satellite_host_filter(
            organization
        ),

        "batch_size": 250,
        "use_reports_api": True,

        # Produce the same familiar host-variable namespaces as AAP.
        "legacy_hostvars": True,
        "want_params": True,
        "want_facts": True,

        # Provider information required for useful filtering later.
        "want_content_facet_attributes": True,
        "want_host_group": True,
        "want_hostcollections": True,
        "want_ipv4": True,
        "want_ipv6": True,
        "want_location": True,
        "want_organization": True,
        "want_smart_proxies": True,
        "want_subnet": True,
        "want_subnet_v6": True,
    }


def _sanitise_ansible_error(
    message,
    inventory_path,
):
    """
    Remove the generated temporary path from an Ansible error.

    The password is never intentionally logged, but we also avoid
    exposing unnecessary temporary-file details.
    """

    message = str(message or "").strip()

    if inventory_path:
        message = message.replace(
            inventory_path,
            "<temporary inventory source>",
        )

    return message


def resolve_foreman_inventory(
    *,
    host,
    username,
    password,
    organization,
    verify_tls=True,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    proxy_url=None,
):
    """
    Resolve a Red Hat Satellite inventory through the Foreman plugin.

    Return the complete parsed output of:

        ansible-inventory --inventory <source>.foreman.yml --list

    The result may contain sensitive facts and host parameters. It
    must not be logged or passed directly to an HTML template.
    """

    if not verify_tls and secure_transport_enforced():
        raise ForemanInventoryError("Satellite TLS certificate verification cannot be disabled.")
    try:
        host = validate_outbound_url(host, purpose="Satellite")
    except OutboundSecurityError as exc:
        raise ForemanInventoryError(str(exc)) from exc

    plugin_config = _build_plugin_config(
        host=host,
        username=username,
        password=password,
        organization=organization,
        verify_tls=verify_tls,
    )

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS

    timeout = max(30, timeout)

    inventory_path = None
    no_redirect_guard_dir = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="journeyman-",
            suffix=".foreman.yml",
            delete=False,
        ) as inventory_file:
            inventory_path = inventory_file.name

            # NamedTemporaryFile normally creates mode 0600, but set it
            # explicitly because this file temporarily contains a
            # credential password.
            os.chmod(
                inventory_path,
                0o600,
            )

            yaml.safe_dump(
                plugin_config,
                inventory_file,
                default_flow_style=False,
                sort_keys=False,
            )

        environment = os.environ.copy()

        # The Foreman inventory plugin uses Python Requests and follows HTTP
        # redirects by default. Inject a subprocess-only sitecustomize module
        # that forces Requests to reject all redirects before any API call.
        no_redirect_guard_dir = _install_no_redirect_guard(
            environment
        )

        # Preserve the configured collection paths used successfully
        # during the manual test.
        if proxy_url:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                environment[key] = proxy_url

        environment.setdefault(
            "ANSIBLE_COLLECTIONS_PATHS",
            (
                "/opt/journeyman/.ansible/collections:"
                "/usr/share/ansible/collections"
            ),
        )

        result = subprocess.run(
            [
                ANSIBLE_INVENTORY_COMMAND,
                "--inventory",
                inventory_path,
                "--list",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=environment,
        )

        if result.returncode != 0:
            error_message = _sanitise_ansible_error(
                result.stderr,
                inventory_path,
            )

            if not error_message:
                error_message = (
                    "ansible-inventory exited with status {}."
                    .format(result.returncode)
                )

            raise ForemanInventoryError(
                "Unable to resolve Satellite inventory: {}".format(
                    error_message
                )
            )

        try:
            inventory = json.loads(
                result.stdout
            )

        except json.JSONDecodeError as exc:
            raise ForemanInventoryError(
                "ansible-inventory returned invalid JSON."
            ) from exc

        if not isinstance(inventory, dict):
            raise ForemanInventoryError(
                "ansible-inventory returned an unexpected result."
            )

        meta = inventory.get("_meta")

        if not isinstance(meta, dict):
            raise ForemanInventoryError(
                "The generated inventory has no _meta section."
            )

        hostvars = meta.get("hostvars")

        if not isinstance(hostvars, dict):
            raise ForemanInventoryError(
                "The generated inventory has no hostvars dictionary."
            )

        return inventory

    except subprocess.TimeoutExpired as exc:
        raise ForemanInventoryError(
            "Satellite inventory resolution exceeded {} seconds."
            .format(timeout)
        ) from exc

    except OSError as exc:
        raise ForemanInventoryError(
            "Unable to execute ansible-inventory: {}".format(
                exc
            )
        ) from exc

    finally:
        if no_redirect_guard_dir:
            shutil.rmtree(
                no_redirect_guard_dir,
                ignore_errors=True,
            )

        # Do not retain a file containing the Satellite password.
        if inventory_path:
            try:
                os.remove(inventory_path)
            except FileNotFoundError:
                pass
            except OSError:
                # Deliberately do not include file content or password.
                pass


def _distribution_display(facts):
    """
    Build a concise operating-system description from Foreman facts.
    """

    if not isinstance(facts, dict):
        return ""

    name = (
        facts.get("distribution::name")
        or facts.get("distribution")
        or ""
    )

    version = (
        facts.get("distribution::version")
        or ""
    )

    parts = [
        str(value).strip()
        for value in (
            name,
            version,
        )
        if value
    ]

    return " ".join(parts)


def _safe_preview_host(
    hostname,
    host_variables,
):
    """
    Derive non-sensitive preview fields from one plugin host.

    This intentionally does not expose ``foreman_params`` or complete
    ``foreman_facts`` to the HTML template.
    """

    if not isinstance(host_variables, dict):
        host_variables = {}

    foreman = host_variables.get(
        "foreman",
        {},
    )

    if not isinstance(foreman, dict):
        foreman = {}

    facts = host_variables.get(
        "foreman_facts",
        {},
    )

    if not isinstance(facts, dict):
        facts = {}

    content_attributes = foreman.get(
        "content_attributes",
        {},
    )

    if not isinstance(content_attributes, dict):
        content_attributes = {}

    lifecycle_environment = (
        content_attributes.get(
            "lifecycle_environment_name"
        )
        or ""
    )

    ipv4 = (
        foreman.get("ipv4")
        or facts.get("network::ipv4_address")
        or ""
    )

    return {
        "name": hostname,
        "ip": ipv4,
        "operating_system": (
            _distribution_display(facts)
        ),
        "hostgroup": (
            foreman.get("host_group")
            or ""
        ),
        "environment": lifecycle_environment,
        "organization": (
            foreman.get("organization")
            or ""
        ),
    }


def preview_satellite_hosts(
    *,
    host,
    username,
    password,
    organization,
    verify_tls=True,
    per_page=None,
):
    """
    Resolve the canonical inventory and return a safe preview.

    ``per_page`` remains accepted for compatibility with the previous
    REST implementation but is not used. The Foreman plugin controls
    pagination through its configured batch size.
    """

    inventory = resolve_foreman_inventory(
        host=host,
        username=username,
        password=password,
        organization=organization,
        verify_tls=verify_tls,
    )

    hostvars = (
        inventory
        .get("_meta", {})
        .get("hostvars", {})
    )

    hosts = [
        _safe_preview_host(
            hostname,
            variables,
        )
        for hostname, variables
        in hostvars.items()
    ]

    hosts.sort(
        key=lambda row: row["name"].lower()
    )

    return {
        # Only safe preview dictionaries are returned here.
        "hosts": hosts,
        "shown": min(
            len(hosts),
            PREVIEW_HOST_LIMIT,
        ),
        "total": len(hosts),
        "organization": {
            "name": organization,
        },
    }
