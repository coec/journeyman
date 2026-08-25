"""
Resolve and refresh Journeyman inventories.

Source inventories are cached locally after an explicit refresh.
Derived inventories are rebuilt locally from their cached dependencies.
"""

import json
import re
from datetime import datetime, timezone

from app import db
from app.credential_types import (
    CREDENTIAL_TYPE_SATELLITE,
    CREDENTIAL_TYPE_ZABBIX,
    CREDENTIAL_TYPE_URL,
)
from app.models.inventory import Inventory
from app.services.filtered_inventory import (
    FilteredInventoryError,
    filter_inventory,
)
from app.services.foreman_inventory import (
    ForemanInventoryError,
    resolve_foreman_inventory,
)
from app.services.zabbix_inventory import (
    ZabbixInventoryError,
    resolve_zabbix_inventory,
)
from app.services.netbox_inventory import NetBoxInventoryError, resolve_netbox_inventory
from app.services.lightspeed_inventory import LightspeedInventoryError, resolve_lightspeed_inventory
from app.services.ovirt_inventory import OvirtInventoryError, resolve_ovirt_inventory
from app.services.url_credentials import URLCredentialError, proxy_url_for_credential, url_credential_details
from app.services.inventory_dependencies import (
    InventoryDependencyError,
    validate_composite_source_lineages,
)
from app.services.inventory_cache import (
    InventoryCacheError,
    InventoryCacheMissingError,
    load_inventory_cache,
    write_inventory_cache,
)
from app.services.static_inventory import (
    StaticInventoryError,
    resolve_static_inventory,
)
from app.services.composite_inventory import (
    CompositeInventoryError,
    append_domain_to_inventory,
    compose_inventories,
)

class InventoryResolutionError(Exception):
    """
    Raised when Journeyman cannot resolve an inventory.
    """




_INVENTORY_BINDING_PATTERN = re.compile(
    r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}"
)


def _binding_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise InventoryResolutionError(
        "Inventory binding values must be scalar."
    )


def _resolve_binding_string(value, bindings, inventory_name):
    value = str(value or "")
    matches = list(_INVENTORY_BINDING_PATTERN.finditer(value))

    # Inventory bindings deliberately support only simple ``{{ identifier }}``
    # substitution. Reject any other Jinja-like construct before attempting
    # substitution so control blocks, comments, attribute access, calls, etc.
    # can never be interpreted as part of the binding language.
    remainder = _INVENTORY_BINDING_PATTERN.sub("", value)
    unsupported_tokens = ("{{", "}}", "{%", "%}", "{#", "#}")
    if any(token in remainder for token in unsupported_tokens):
        raise InventoryResolutionError(
            'Inventory "{}" contains an unsupported inventory binding expression.'
            .format(inventory_name)
        )

    if not matches:
        return value

    bindings = bindings or {}

    def replace(match):
        name = match.group(1)
        if name not in bindings:
            raise InventoryResolutionError(
                'Inventory "{}" requires inventory binding "{}".'
                .format(inventory_name, name)
            )
        return _binding_text(bindings[name])

    resolved = _INVENTORY_BINDING_PATTERN.sub(replace, value)
    if "{{" in resolved or "}}" in resolved:
        raise InventoryResolutionError(
            'Inventory "{}" contains an unsupported inventory binding expression.'
            .format(inventory_name)
        )
    return resolved


def _resolve_filter_groups_bindings(groups, bindings, inventory_name):
    if groups is None:
        return None

    resolved_groups = []
    for group in groups:
        if not isinstance(group, dict):
            resolved_groups.append(group)
            continue

        resolved_group = dict(group)
        rules = []
        for rule in group.get("rules", []):
            if not isinstance(rule, dict):
                rules.append(rule)
                continue

            resolved_rule = dict(rule)
            for field_name in ("parameter", "value"):
                if field_name in resolved_rule:
                    resolved_rule[field_name] = _resolve_binding_string(
                        resolved_rule.get(field_name),
                        bindings,
                        inventory_name,
                    )
            rules.append(resolved_rule)

        resolved_group["rules"] = rules
        resolved_groups.append(resolved_group)

    return resolved_groups


def _resolve_filter_rules_bindings(rules, bindings, inventory_name):
    if rules is None:
        return None

    resolved = []
    for rule in rules:
        if not isinstance(rule, dict):
            resolved.append(rule)
            continue

        resolved_rule = dict(rule)
        for field_name in ("parameter", "value"):
            if field_name in resolved_rule:
                resolved_rule[field_name] = _resolve_binding_string(
                    resolved_rule.get(field_name),
                    bindings,
                    inventory_name,
                )
        resolved.append(resolved_rule)

    return resolved

def inventory_config(inventory):
    """
    Decode and return an Inventory's configuration.
    """

    value = inventory.config_json or "{}"

    try:
        config = json.loads(value)

    except (TypeError, ValueError) as exc:
        raise InventoryResolutionError(
            'Inventory "{}" contains invalid configuration JSON.'
            .format(inventory.name)
        ) from exc

    if not isinstance(config, dict):
        raise InventoryResolutionError(
            'Inventory "{}" configuration must be an object.'
            .format(inventory.name)
        )

    return config


def _visited_inventory_ids(
    inventory,
    visited_inventory_ids,
):
    """
    Validate one dependency traversal and return its updated path.
    """

    if inventory is None:
        raise InventoryResolutionError(
            "No inventory was supplied."
        )

    if not inventory.enabled:
        raise InventoryResolutionError(
            'Inventory "{}" is disabled.'
            .format(inventory.name)
        )

    if visited_inventory_ids is None:
        visited_inventory_ids = set()

    if inventory.id in visited_inventory_ids:
        raise InventoryResolutionError(
            'Inventory dependency loop detected at "{}".'
            .format(inventory.name)
        )

    updated = set(
        visited_inventory_ids
    )

    updated.add(
        inventory.id
    )

    return updated



def _inventory_proxy_url(inventory):
    config = inventory_config(inventory)
    proxy_id = config.get("proxy_credential_id")
    if not proxy_id:
        return None
    proxy_credential = db.session.get(__import__("app.models.credential", fromlist=["Credential"]).Credential, int(proxy_id))
    if proxy_credential is None:
        raise InventoryResolutionError('Inventory "{}" references a missing proxy credential.'.format(inventory.name))
    try:
        return proxy_url_for_credential(proxy_credential)
    except (URLCredentialError, TypeError, ValueError) as exc:
        raise InventoryResolutionError(str(exc)) from exc

def _resolve_foreman_inventory_live(inventory):
    """
    Query Red Hat Satellite using the Foreman inventory plugin.
    """

    credential = inventory.credential

    if credential is None:
        raise InventoryResolutionError(
            'Inventory "{}" has no credential.'
            .format(inventory.name)
        )

    if credential.credential_type not in {CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_URL}:
        raise InventoryResolutionError(
            'Inventory "{}" requires a URL / API or legacy Red Hat Satellite credential.'.format(inventory.name)
        )

    try:
        credential_data = (
            credential.get_credential_data()
        )

    except Exception as exc:
        raise InventoryResolutionError(
            'Unable to decrypt the credential used by "{}".'
            .format(inventory.name)
        ) from exc

    config = inventory_config(
        inventory
    )

    organization = str(
        config.get("organization") or ""
    ).strip()

    if not organization:
        raise InventoryResolutionError(
            'Inventory "{}" has no Satellite organization.'
            .format(inventory.name)
        )

    try:
        if credential.credential_type == CREDENTIAL_TYPE_URL:
            try:
                _url_username, url_data = url_credential_details(credential)
            except URLCredentialError as exc:
                raise InventoryResolutionError(str(exc)) from exc
            if url_data.get("auth_mode") != "basic":
                raise InventoryResolutionError("Red Hat Satellite requires a URL credential using HTTP Basic authentication.")
            satellite_host = url_data.get("url")
            satellite_password = url_data.get("password")
        else:
            satellite_host = credential_data.get("host")
            satellite_password = credential_data.get("password")
        return resolve_foreman_inventory(
            host=satellite_host,
            username=credential.username,
            password=satellite_password,
            organization=organization,
            verify_tls=inventory.verify_tls,
            proxy_url=_inventory_proxy_url(inventory),
        )

    except ForemanInventoryError as exc:
        raise InventoryResolutionError(
            str(exc)
        ) from exc

def _resolve_zabbix_inventory_live(inventory):
    """
    Query Zabbix using a token-only credential.
    """

    credential = inventory.credential

    if credential is None:
        raise InventoryResolutionError(
            'Inventory "{}" has no credential.'
            .format(inventory.name)
        )

    if credential.credential_type not in {CREDENTIAL_TYPE_ZABBIX, CREDENTIAL_TYPE_URL}:
        raise InventoryResolutionError(
            'Inventory "{}" requires a URL / API or legacy Zabbix token credential.'.format(inventory.name)
        )

    try:
        credential_data = (
            credential.get_credential_data()
        )

    except Exception as exc:
        raise InventoryResolutionError(
            'Unable to decrypt the credential used by "{}".'
            .format(inventory.name)
        ) from exc

    if credential.credential_type == CREDENTIAL_TYPE_URL:
        try:
            _url_username, url_data = url_credential_details(credential)
        except URLCredentialError as exc:
            raise InventoryResolutionError(str(exc)) from exc
        if url_data.get("auth_mode") not in {"bearer", "token"}:
            raise InventoryResolutionError("Zabbix requires a token-based URL credential.")
        token = url_data.get("token")
        endpoint = url_data.get("url")
    else:
        token = credential_data.get("token")
        endpoint = inventory.endpoint

    config = inventory_config(
        inventory
    )

    try:
        return resolve_zabbix_inventory(
            endpoint=endpoint,
            token=token,
            tag_name=config.get(
                "tag_name"
            ),
            tag_value=config.get(
                "tag_value"
            ),
            verify_tls=inventory.verify_tls,
            proxy_url=_inventory_proxy_url(inventory),
            include_disabled=bool(
                config.get(
                    "include_disabled",
                    False,
                )
            ),
        )

    except ZabbixInventoryError as exc:
        raise InventoryResolutionError(
            str(exc)
        ) from exc

def _resolve_netbox_inventory_live(inventory):
    if inventory.credential is None or inventory.credential.credential_type != CREDENTIAL_TYPE_URL:
        raise InventoryResolutionError('Inventory "{}" requires a URL / API credential.'.format(inventory.name))
    config = inventory_config(inventory)
    try:
        return resolve_netbox_inventory(
            credential=inventory.credential, verify_tls=inventory.verify_tls,
            status=config.get("status", "active"), tag=config.get("tag", ""),
            site=config.get("site", ""), role=config.get("role", ""),
            interfaces=config.get("interfaces", True),
            services=config.get("services", True),
            config_context=config.get("config_context", True),
            site_data=config.get("site_data", True),
            virtual_disks=config.get("virtual_disks", True),
            proxy_url=_inventory_proxy_url(inventory),
        )
    except NetBoxInventoryError as exc:
        raise InventoryResolutionError(str(exc)) from exc


def _resolve_lightspeed_inventory_live(inventory):
    if inventory.credential is None or inventory.credential.credential_type != CREDENTIAL_TYPE_URL:
        raise InventoryResolutionError('Inventory "{}" requires a URL / API credential.'.format(inventory.name))
    config = inventory_config(inventory)
    try:
        return resolve_lightspeed_inventory(
            credential=inventory.credential, verify_tls=inventory.verify_tls,
            tags=config.get("tags", ""),
            proxy_url=_inventory_proxy_url(inventory),
        )
    except LightspeedInventoryError as exc:
        raise InventoryResolutionError(str(exc)) from exc



def _resolve_ovirt_inventory_live(inventory):
    if inventory.credential is None or inventory.credential.credential_type != CREDENTIAL_TYPE_URL:
        raise InventoryResolutionError(
            'Inventory "{}" requires a URL / API credential.'.format(inventory.name)
        )
    config = inventory_config(inventory)
    try:
        return resolve_ovirt_inventory(
            credential=inventory.credential,
            verify_tls=inventory.verify_tls,
            query_filter=config.get("query_filter"),
            hostname_preference=config.get("hostname_preference") or ["fqdn", "name"],
            proxy_url=_inventory_proxy_url(inventory),
        )
    except OvirtInventoryError as exc:
        raise InventoryResolutionError(str(exc)) from exc


def _resolve_static_inventory_live(inventory):
    """
    Rebuild a static inventory from its stored YAML.
    """

    config = inventory_config(
        inventory
    )

    try:
        return resolve_static_inventory(
            content=config.get(
                "content",
                "",
            )
        )

    except StaticInventoryError as exc:
        raise InventoryResolutionError(
            str(exc)
        ) from exc


def _filtered_source_inventory(inventory):
    """
    Return the direct source used by a filtered inventory.
    """

    config = inventory_config(
        inventory
    )

    source_inventory_id = config.get(
        "source_inventory_id"
    )

    try:
        source_inventory_id = int(
            source_inventory_id
        )

    except (TypeError, ValueError) as exc:
        raise InventoryResolutionError(
            'Filtered inventory "{}" has no valid source inventory.'
            .format(inventory.name)
        ) from exc

    source_inventory = db.session.get(
        Inventory,
        source_inventory_id,
    )

    if source_inventory is None:
        raise InventoryResolutionError(
            'The source inventory used by "{}" no longer exists.'
            .format(inventory.name)
        )

    return source_inventory

def _composite_source_inventories(inventory):
    """
    Return the inventories directly used by a composite inventory.
    """

    config = inventory_config(
        inventory
    )

    source_inventory_ids = config.get(
        "source_inventory_ids",
        [],
    )

    if not isinstance(source_inventory_ids, list):
        raise InventoryResolutionError(
            'Composite inventory "{}" source inventories '
            "must be a list.".format(
                inventory.name
            )
        )

    source_inventories = []
    seen_inventory_ids = set()

    for source_inventory_id in source_inventory_ids:
        try:
            source_inventory_id = int(
                source_inventory_id
            )

        except (TypeError, ValueError) as exc:
            raise InventoryResolutionError(
                'Composite inventory "{}" contains an '
                "invalid source inventory ID.".format(
                    inventory.name
                )
            ) from exc

        if source_inventory_id == inventory.id:
            raise InventoryResolutionError(
                'Composite inventory "{}" cannot use itself '
                "as a source.".format(
                    inventory.name
                )
            )

        if source_inventory_id in seen_inventory_ids:
            continue

        seen_inventory_ids.add(
            source_inventory_id
        )

        source_inventory = db.session.get(
            Inventory,
            source_inventory_id,
        )

        if source_inventory is None:
            raise InventoryResolutionError(
                'A source inventory used by "{}" no longer '
                "exists.".format(
                    inventory.name
                )
            )

        source_inventories.append(
            source_inventory
        )

    if len(source_inventories) < 2:
        raise InventoryResolutionError(
            'Composite inventory "{}" requires at least '
            "two source inventories.".format(
                inventory.name
            )
        )

    try:
        validate_composite_source_lineages(
            [source.id for source in source_inventories]
        )
    except InventoryDependencyError as exc:
        raise InventoryResolutionError(str(exc)) from exc

    return source_inventories


def _apply_composite_inventory(
    inventory,
    resolved_sources,
):
    """
    Combine the resolved sources used by a composite inventory.
    """

    try:
        config = inventory_config(inventory)
        return compose_inventories(
            resolved_sources,
            normalize_hostnames=config.get(
                "normalize_hostnames",
                "none",
            ),
        )

    except CompositeInventoryError as exc:
        raise InventoryResolutionError(
            'Unable to compose inventory "{}": {}'
            .format(
                inventory.name,
                exc,
            )
        ) from exc

def _apply_filtered_inventory(
    inventory,
    source_data,
    *,
    bindings=None,
):
    """
    Apply one filtered inventory's rules.
    """

    config = inventory_config(
        inventory
    )

    try:
        return filter_inventory(
            source_data,
            include_groups=_resolve_filter_groups_bindings(
                config.get("include_groups"),
                bindings,
                inventory.name,
            ),
            exclude_groups=_resolve_filter_groups_bindings(
                config.get("exclude_groups"),
                bindings,
                inventory.name,
            ),
            include_rules=_resolve_filter_rules_bindings(
                config.get("include", []),
                bindings,
                inventory.name,
            ),
            exclude_rules=_resolve_filter_rules_bindings(
                config.get("exclude", []),
                bindings,
                inventory.name,
            ),
        )

    except FilteredInventoryError as exc:
        raise InventoryResolutionError(
            'Unable to filter inventory "{}": {}'
            .format(
                inventory.name,
                exc,
            )
        ) from exc


def _resolve_cached_source_inventory(inventory):
    """
    Read one source inventory from its local canonical cache.
    """

    try:
        return load_inventory_cache(
            inventory
        )

    except InventoryCacheMissingError as exc:
        raise InventoryResolutionError(
            '{} Use the Refresh action first.'
            .format(exc)
        ) from exc

    except InventoryCacheError as exc:
        raise InventoryResolutionError(
            str(exc)
        ) from exc


def _record_refresh_success(inventory):
    """
    Record a successful source or dependency refresh.
    """

    inventory.status = "ready"
    inventory.last_sync_at = datetime.now(
        timezone.utc
    )

    try:
        db.session.commit()

    except Exception as exc:
        db.session.rollback()

        raise InventoryResolutionError(
            'Inventory "{}" refreshed, but its status '
            "could not be saved.".format(
                inventory.name
            )
        ) from exc


def _record_refresh_failure(inventory):
    """
    Record refresh failure while retaining any previous cache.
    """

    try:
        inventory.status = "refresh_failed"
        db.session.commit()

    except Exception:
        db.session.rollback()


def _apply_inventory_output_transforms(inventory, inventory_data):
    """Apply non-destructive transforms to one resolved inventory result."""

    config = inventory_config(inventory)
    append_domain = config.get("append_domain", "")
    if not append_domain:
        return inventory_data

    try:
        return append_domain_to_inventory(
            inventory_data,
            append_domain,
            inventory_name=inventory.name,
        )
    except CompositeInventoryError as exc:
        raise InventoryResolutionError(
            'Unable to append the default domain for inventory "{}": {}'
            .format(inventory.name, exc)
        ) from exc


def resolve_inventory(
    inventory,
    *,
    visited_inventory_ids=None,
    bindings=None,
):
    """
    Resolve an inventory without contacting external providers.

    Source inventories are read from local cache. Derived inventories
    are calculated locally from those cached sources. Output-only hostname
    transforms are applied after the inventory itself has resolved.
    """

    visited_inventory_ids = _visited_inventory_ids(
        inventory,
        visited_inventory_ids,
    )

    if inventory.inventory_type in {
        "satellite",
        "zabbix",
        "netbox",
        "lightspeed",
        "ovirt",
    }:
        inventory_data = _resolve_cached_source_inventory(inventory)

    elif inventory.inventory_type == "static":
        inventory_data = _resolve_static_inventory_live(inventory)

    elif inventory.inventory_type == "filtered":
        source_inventory = _filtered_source_inventory(inventory)
        source_data = resolve_inventory(
            source_inventory,
            visited_inventory_ids=visited_inventory_ids,
            bindings=bindings,
        )
        inventory_data = _apply_filtered_inventory(
            inventory,
            source_data,
            bindings=bindings,
        )

    elif inventory.inventory_type == "composite":
        source_inventories = _composite_source_inventories(inventory)
        resolved_sources = []
        for source_inventory in source_inventories:
            source_data = resolve_inventory(
                source_inventory,
                visited_inventory_ids=visited_inventory_ids,
                bindings=bindings,
            )
            resolved_sources.append((source_inventory.name, source_data))
        inventory_data = _apply_composite_inventory(inventory, resolved_sources)

    else:
        raise InventoryResolutionError(
            'Unsupported inventory type "{}".'.format(inventory.inventory_type)
        )

    return _apply_inventory_output_transforms(inventory, inventory_data)


def refresh_inventory(
    inventory,
    *,
    visited_inventory_ids=None,
    refreshed_inventory_data=None,
    bindings=None,
):
    """
    Refresh source dependencies and return resolved inventory data.

    Source inventories contact their provider and replace their local
    cache only after a successful resolution.

    Filtered inventories recursively refresh their source and then
    apply their rules locally.
    """

    if refreshed_inventory_data is None:
        refreshed_inventory_data = {}

    if inventory.id in refreshed_inventory_data:
        return refreshed_inventory_data[
            inventory.id
        ]

    visited_inventory_ids = (
        _visited_inventory_ids(
            inventory,
            visited_inventory_ids,
        )
    )

    try:
        if inventory.inventory_type == "satellite":
            inventory_data = (
                _resolve_foreman_inventory_live(
                    inventory
                )
            )

            try:
                write_inventory_cache(
                    inventory,
                    inventory_data,
                )

            except InventoryCacheError as exc:
                raise InventoryResolutionError(
                    str(exc)
                ) from exc

        elif inventory.inventory_type == "zabbix":
            inventory_data = (
                _resolve_zabbix_inventory_live(
                    inventory
                )
            )

            try:
                write_inventory_cache(
                    inventory,
                    inventory_data,
                )

            except InventoryCacheError as exc:
                raise InventoryResolutionError(
                    str(exc)
                ) from exc

        elif inventory.inventory_type == "netbox":
            inventory_data = _resolve_netbox_inventory_live(inventory)
            try:
                write_inventory_cache(inventory, inventory_data)
            except InventoryCacheError as exc:
                raise InventoryResolutionError(str(exc)) from exc

        elif inventory.inventory_type == "lightspeed":
            inventory_data = _resolve_lightspeed_inventory_live(inventory)
            try:
                write_inventory_cache(inventory, inventory_data)
            except InventoryCacheError as exc:
                raise InventoryResolutionError(str(exc)) from exc

        elif inventory.inventory_type == "ovirt":
            inventory_data = _resolve_ovirt_inventory_live(inventory)
            try:
                write_inventory_cache(inventory, inventory_data)
            except InventoryCacheError as exc:
                raise InventoryResolutionError(str(exc)) from exc

        elif inventory.inventory_type == "static":
            inventory_data = (
                _resolve_static_inventory_live(
                    inventory
                )
            )

            try:
                write_inventory_cache(
                    inventory,
                    inventory_data,
                )

            except InventoryCacheError as exc:
                raise InventoryResolutionError(
                    str(exc)
                ) from exc

        elif inventory.inventory_type == "filtered":
            source_inventory = (
                _filtered_source_inventory(
                    inventory
                )
            )

            source_data = refresh_inventory(
                source_inventory,
                visited_inventory_ids=(
                    visited_inventory_ids
                ),
                refreshed_inventory_data=(
                    refreshed_inventory_data
                ),
                bindings=bindings,
            )

            inventory_data = (
                _apply_filtered_inventory(
                    inventory,
                    source_data,
                    bindings=bindings,
                )
            )

        elif inventory.inventory_type == "composite":
            source_inventories = (
                _composite_source_inventories(
                    inventory
                )
            )

            resolved_sources = []

            for source_inventory in source_inventories:
                source_data = refresh_inventory(
                    source_inventory,
                    visited_inventory_ids=(
                        visited_inventory_ids
                    ),
                    refreshed_inventory_data=(
                        refreshed_inventory_data
                    ),
                    bindings=bindings,
                )

                resolved_sources.append(
                    (
                        source_inventory.name,
                        source_data,
                    )
                )

            inventory_data = (
                _apply_composite_inventory(
                    inventory,
                    resolved_sources,
                )
            )

        else:
            raise InventoryResolutionError(
                'Unsupported inventory type "{}".'
                .format(inventory.inventory_type)
            )

        inventory_data = _apply_inventory_output_transforms(
            inventory,
            inventory_data,
        )

        refreshed_inventory_data[
            inventory.id
        ] = inventory_data

        _record_refresh_success(
            inventory
        )

        return inventory_data

    except InventoryResolutionError:
        _record_refresh_failure(
            inventory
        )

        raise

    except Exception as exc:
        _record_refresh_failure(
            inventory
        )

        raise InventoryResolutionError(
            'Unexpected error refreshing inventory "{}".'
            .format(inventory.name)
        ) from exc
