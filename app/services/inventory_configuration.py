"""Declarative Inventory configuration shared by the REST API and Ansible clients."""

from dataclasses import dataclass
import json

import yaml

from app import db
from app.credential_types import CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_ZABBIX, CREDENTIAL_TYPE_URL
from app.models import Credential, Inventory, Project, ProjectStep
from app.services.inventory_cache import InventoryCacheError, delete_inventory_cache
from app.services.composite_inventory import CompositeInventoryError, normalise_default_domain
from app.services.inventory_dependencies import (
    InventoryDependencyError,
    direct_inventory_dependants,
    validate_inventory_dependency_update,
)
from app.services.name_ordering import reserved_name_validation_error
from app.services.outbound_security import OutboundSecurityError, validate_outbound_url
from app.services.url_credentials import URLCredentialError, proxy_url_for_credential


class InventoryConfigurationError(ValueError):
    pass


@dataclass
class InventoryConfigurationResult:
    changed: bool
    inventory: Inventory | None
    message: str


_FILTER_FIELDS = frozenset({
    "hostname", "group", "foreman_param", "variable",
})
_FILTER_OPERATORS = frozenset({
    "equals", "not_equals", "contains", "not_contains",
    "starts_with", "ends_with", "regex", "not_regex",
    "exists", "not_exists",
})
_SUPPORTED_TYPES = frozenset({"satellite", "static", "filtered", "composite", "zabbix", "netbox", "lightspeed", "ovirt"})


def _clean(value):
    return str(value or "").strip()


def _lookup_inventory(name, label="Inventory"):
    value = _clean(name)
    if not value:
        return None
    row = Inventory.query.filter_by(name=value).one_or_none()
    if row is None:
        raise InventoryConfigurationError(f'{label} "{value}" does not exist.')
    return row


def _lookup_credential(name, expected_type, label):
    value = _clean(name)
    if not value:
        raise InventoryConfigurationError(f"A {label} credential is required.")
    row = Credential.query.filter_by(name=value).one_or_none()
    expected_types = {expected_type} if isinstance(expected_type, str) else set(expected_type)
    if row is None or row.credential_type not in expected_types:
        raise InventoryConfigurationError(f'{label} credential "{value}" is missing or invalid.')
    return row


def _normalise_filter_groups(groups, label):
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise InventoryConfigurationError(f"{label} groups must be a list.")
    result = []
    for group_index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise InventoryConfigurationError(f"{label} group {group_index} is invalid.")
        match = _clean(group.get("match")).lower()
        if match not in {"all", "any"}:
            raise InventoryConfigurationError(f"{label} group {group_index} must match ALL or ANY rules.")
        rules = group.get("rules")
        if not isinstance(rules, list) or not rules:
            raise InventoryConfigurationError(f"{label} group {group_index} must contain at least one rule.")
        normalised_rules = []
        for rule_index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                raise InventoryConfigurationError(f"{label} group {group_index} rule {rule_index} is invalid.")
            field = _clean(rule.get("field"))
            operator = _clean(rule.get("operator"))
            parameter = _clean(rule.get("parameter"))
            value = _clean(rule.get("value"))
            prefix = f"{label} group {group_index} rule {rule_index}"
            if field not in _FILTER_FIELDS:
                raise InventoryConfigurationError(f"{prefix} has an invalid field.")
            if operator not in _FILTER_OPERATORS:
                raise InventoryConfigurationError(f"{prefix} has an invalid operator.")
            if field == "foreman_param" and not parameter:
                raise InventoryConfigurationError(f"{prefix} requires a Satellite host parameter name.")
            if field == "variable" and not parameter:
                raise InventoryConfigurationError(f"{prefix} requires a host variable path.")
            if operator not in {"exists", "not_exists"} and not value:
                raise InventoryConfigurationError(f"{prefix} requires a value.")
            normalised_rules.append({
                "field": field,
                "operator": operator,
                "parameter": parameter,
                "value": value,
            })
        result.append({"match": match, "rules": normalised_rules})
    return result


def _normalise(inventory, values):
    name = _clean(values.get("name"))
    if not name:
        raise InventoryConfigurationError("Name is required.")
    reserved = reserved_name_validation_error(
        name,
        existing_name=inventory.name if inventory is not None else None,
    )
    if reserved:
        raise InventoryConfigurationError(reserved)

    inventory_type = _clean(values.get("inventory_type")) or "static"
    if inventory_type not in _SUPPORTED_TYPES:
        raise InventoryConfigurationError("A valid inventory type is required.")
    if inventory is not None and inventory.inventory_type != inventory_type:
        raise InventoryConfigurationError("Inventory type cannot be changed after creation.")

    enabled = bool(values.get("enabled", True))
    endpoint = ""
    credential_id = None
    verify_tls = True
    config = {}

    try:
        append_domain = normalise_default_domain(values.get("append_domain"))
    except CompositeInventoryError as exc:
        raise InventoryConfigurationError(str(exc)) from exc

    if inventory_type == "static":
        content = str(values.get("content") or "").strip()
        if not content:
            raise InventoryConfigurationError("Static inventory YAML is required.")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise InventoryConfigurationError(f"Static inventory YAML is invalid: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InventoryConfigurationError("Static inventory YAML must contain an inventory mapping.")
        config = {"content": content}

    elif inventory_type == "satellite":
        organization = _clean(values.get("organization"))
        if not organization:
            raise InventoryConfigurationError("Satellite organization is required.")
        credential = _lookup_credential(
            values.get("credential"), {CREDENTIAL_TYPE_URL, CREDENTIAL_TYPE_SATELLITE}, "Red Hat Satellite"
        )
        credential_id = credential.id
        verify_tls = bool(values.get("verify_tls", True))
        config = {"organization": organization}

    elif inventory_type == "zabbix":
        credential = _lookup_credential(
            values.get("credential"), {CREDENTIAL_TYPE_URL, CREDENTIAL_TYPE_ZABBIX}, "Zabbix"
        )
        credential_id = credential.id
        verify_tls = bool(values.get("verify_tls", True))
        if credential.credential_type == CREDENTIAL_TYPE_ZABBIX:
            endpoint = _clean(values.get("endpoint")).rstrip("/")
            if not endpoint:
                raise InventoryConfigurationError("Legacy Zabbix credentials require a Zabbix API URL.")
            try:
                endpoint = validate_outbound_url(endpoint, purpose="Zabbix API")
            except OutboundSecurityError as exc:
                raise InventoryConfigurationError(str(exc)) from exc
        tag_name = _clean(values.get("tag_name"))
        tag_value = _clean(values.get("tag_value"))
        if not tag_name:
            raise InventoryConfigurationError("Zabbix host tag name is required.")
        if not tag_value:
            raise InventoryConfigurationError("Zabbix host tag value is required.")
        config = {
            "tag_name": tag_name,
            "tag_value": tag_value,
            "include_disabled": bool(values.get("include_disabled", False)),
        }

    elif inventory_type == "netbox":
        credential = _lookup_credential(values.get("credential"), CREDENTIAL_TYPE_URL, "NetBox URL / API")
        credential_id = credential.id
        verify_tls = bool(values.get("verify_tls", True))
        try:
            _username, credential_data = credential.username or "", credential.get_credential_data()
        except Exception as exc:
            raise InventoryConfigurationError("Unable to decrypt NetBox credential.") from exc
        if str(credential_data.get("auth_mode") or "").lower() != "token":
            raise InventoryConfigurationError("NetBox requires a URL / API credential using Token authentication.")
        config = {
            "status": _clean(values.get("status")) or "active",
            "tag": _clean(values.get("tag")),
            "site": _clean(values.get("site")),
            "role": _clean(values.get("role")),
            "interfaces": bool(values.get("interfaces", True)),
            "services": bool(values.get("services", True)),
            "config_context": bool(values.get("config_context", True)),
            "site_data": bool(values.get("site_data", True)),
            "virtual_disks": bool(values.get("virtual_disks", True)),
        }

    elif inventory_type == "lightspeed":
        credential = _lookup_credential(values.get("credential"), CREDENTIAL_TYPE_URL, "Red Hat Lightspeed URL / API")
        credential_id = credential.id
        verify_tls = bool(values.get("verify_tls", True))
        config = {"tags": _clean(values.get("tags"))}

    elif inventory_type == "ovirt":
        credential = _lookup_credential(values.get("credential"), CREDENTIAL_TYPE_URL, "oVirt / RHV URL / API")
        credential_id = credential.id
        verify_tls = bool(values.get("verify_tls", True))
        try:
            _username, credential_data = credential.username or "", credential.get_credential_data()
        except Exception as exc:
            raise InventoryConfigurationError("Unable to decrypt oVirt / RHV credential.") from exc
        if str(credential_data.get("auth_mode") or "").lower() != "basic":
            raise InventoryConfigurationError("oVirt / RHV requires a URL / API credential using Basic authentication.")
        query_filter = values.get("query_filter")
        if query_filter is not None and not isinstance(query_filter, dict):
            raise InventoryConfigurationError("oVirt / RHV query_filter must be a mapping.")
        preference = values.get("hostname_preference") or ["fqdn", "name"]
        if not isinstance(preference, list) or not all(_clean(item) for item in preference):
            raise InventoryConfigurationError("oVirt / RHV hostname_preference must be a list of attribute names.")
        config = {
            "query_filter": query_filter or None,
            "hostname_preference": [_clean(item) for item in preference],
        }

    elif inventory_type == "filtered":
        source = _lookup_inventory(values.get("source_inventory"), "Source inventory")
        if source is None:
            raise InventoryConfigurationError("A source inventory is required.")
        if inventory is not None and source.id == inventory.id:
            raise InventoryConfigurationError("An inventory cannot use itself as its source.")
        if not source.enabled:
            raise InventoryConfigurationError(f'Source inventory "{source.name}" is disabled.')
        try:
            validate_inventory_dependency_update(inventory.id if inventory else None, [source.id])
        except InventoryDependencyError as exc:
            raise InventoryConfigurationError(str(exc)) from exc
        config = {
            "source_inventory_id": source.id,
            "include_groups": _normalise_filter_groups(values.get("include_groups", []), "Include"),
            "exclude_groups": _normalise_filter_groups(values.get("exclude_groups", []), "Exclude"),
        }

    elif inventory_type == "composite":
        names = values.get("source_inventories")
        if not isinstance(names, list):
            raise InventoryConfigurationError("Composite source inventories must be a list of names.")
        sources = []
        seen = set()
        for item in names:
            source = _lookup_inventory(item, "Source inventory")
            if source is None or source.id in seen:
                continue
            if inventory is not None and source.id == inventory.id:
                raise InventoryConfigurationError("An inventory cannot use itself as a source.")
            if not source.enabled:
                raise InventoryConfigurationError(f'Source inventory "{source.name}" is disabled.')
            sources.append(source)
            seen.add(source.id)
        if len(sources) < 2:
            raise InventoryConfigurationError("A Composite Inventory requires at least two source inventories.")
        try:
            source_ids = validate_inventory_dependency_update(
                inventory.id if inventory else None,
                [row.id for row in sources],
            )
        except InventoryDependencyError as exc:
            raise InventoryConfigurationError(str(exc)) from exc
        normalize_hostnames = _clean(values.get("normalize_hostnames")) or "none"
        if normalize_hostnames not in {"none", "short", "fqdn"}:
            raise InventoryConfigurationError(
                "Composite hostname normalization must be none, short, or fqdn."
            )
        config = {
            "source_inventory_ids": source_ids,
            "normalize_hostnames": normalize_hostnames,
        }

    if inventory_type in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}:
        proxy_name = _clean(values.get("proxy_credential"))
        if proxy_name:
            proxy_credential = _lookup_credential(
                proxy_name, CREDENTIAL_TYPE_URL, "Inventory proxy"
            )
            try:
                proxy_url_for_credential(proxy_credential)
            except URLCredentialError as exc:
                raise InventoryConfigurationError(str(exc)) from exc
            config["proxy_credential_id"] = proxy_credential.id

    config["append_domain"] = append_domain

    return {
        "name": name,
        "inventory_type": inventory_type,
        "endpoint": endpoint,
        "credential_id": credential_id,
        "verify_tls": verify_tls,
        "enabled": enabled,
        "config_json": json.dumps(config, sort_keys=True),
    }


def configure_inventory(values):
    name = _clean(values.get("name"))
    inventory = Inventory.query.filter_by(name=name).one_or_none() if name else None
    desired = _normalise(inventory, values)
    created = inventory is None
    if created:
        inventory = Inventory(name=desired["name"], inventory_type=desired["inventory_type"])
        db.session.add(inventory)

    changed = created or any(getattr(inventory, key) != value for key, value in desired.items())
    source_changed = any(
        getattr(inventory, key) != desired[key]
        for key in ("endpoint", "credential_id", "verify_tls", "config_json")
    ) if not created else True

    for key, value in desired.items():
        setattr(inventory, key, value)

    if source_changed:
        inventory.status = "never_synced"
        inventory.last_sync_at = None

    if changed:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise InventoryConfigurationError("Inventory name must be unique.") from exc

    return InventoryConfigurationResult(
        changed=changed,
        inventory=inventory,
        message=(
            f'Inventory "{inventory.name}" created.' if created else
            (f'Inventory "{inventory.name}" updated.' if changed else f'Inventory "{inventory.name}" is already configured.')
        ),
    )


def delete_inventory(name):
    inventory = Inventory.query.filter_by(name=_clean(name)).one_or_none()
    if inventory is None:
        return InventoryConfigurationResult(False, None, f'Inventory "{_clean(name)}" is already absent.')

    if Project.query.filter_by(inventory_id=inventory.id).first() is not None:
        raise InventoryConfigurationError(
            f'Inventory "{inventory.name}" cannot be deleted because it is assigned to one or more projects.'
        )
    if ProjectStep.query.filter_by(inventory_id=inventory.id).first() is not None:
        raise InventoryConfigurationError(
            f'Inventory "{inventory.name}" cannot be deleted because it is used as a project step override.'
        )
    try:
        dependants = direct_inventory_dependants(inventory.id)
    except InventoryDependencyError as exc:
        raise InventoryConfigurationError(str(exc)) from exc
    if dependants:
        raise InventoryConfigurationError(
            'Inventory "{}" cannot be deleted because it is used by: {}.'.format(
                inventory.name, ", ".join(row.name for row in dependants)
            )
        )

    inventory_name = inventory.name
    try:
        delete_inventory_cache(inventory)
    except InventoryCacheError as exc:
        raise InventoryConfigurationError(f'Unable to delete Inventory "{inventory_name}" cache: {exc}') from exc
    db.session.delete(inventory)
    db.session.commit()
    return InventoryConfigurationResult(True, None, f'Inventory "{inventory_name}" deleted.')
