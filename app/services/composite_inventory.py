"""
Combine canonical Ansible inventories.

Hosts, groups and list values are de-duplicated. Dictionaries are
merged recursively. Conflicting scalar values are rejected rather than
silently allowing one inventory to overwrite another.
"""

from collections import defaultdict
from copy import deepcopy
import re


class CompositeInventoryError(Exception):
    """
    Raised when inventories cannot be safely combined.
    """



NORMALIZE_NONE = "none"
NORMALIZE_SHORT = "short"
NORMALIZE_FQDN = "fqdn"
NORMALIZE_MODES = {
    NORMALIZE_NONE,
    NORMALIZE_SHORT,
    NORMALIZE_FQDN,
}

_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def normalise_default_domain(value):
    """Return a validated DNS suffix suitable for appending to short names."""

    domain = str(value or "").strip()
    if not domain:
        return ""
    if len(domain) > 253 or domain.startswith(".") or domain.endswith("."):
        raise CompositeInventoryError(
            "Default domain must be an unqualified DNS suffix such as example.com."
        )

    labels = domain.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        raise CompositeInventoryError(
            "Default domain must be a valid DNS domain such as example.com."
        )

    return domain.casefold()


def _inventory_hostnames(inventory_data):
    """Return every hostname represented by an inventory."""

    if not isinstance(inventory_data, dict):
        return set()

    meta = inventory_data.get("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    hostvars = meta.get("hostvars", {})
    if not isinstance(hostvars, dict):
        hostvars = {}

    hostnames = set(hostvars.keys())
    hostnames.update(_all_group_hosts(inventory_data))
    return {
        hostname
        for hostname in hostnames
        if isinstance(hostname, str) and hostname
    }


def _normalization_map(source_inventories, mode):
    """Build conservative short-name/FQDN aliases across sources.

    A short name is matched only when exactly one distinct FQDN with the same
    first DNS label exists across the composite sources, and the short and
    FQDN forms do not coexist in any one source.  Ambiguous names are left
    untouched rather than guessed.
    """

    if mode == NORMALIZE_NONE:
        return {}

    if mode not in NORMALIZE_MODES:
        raise CompositeInventoryError(
            'Unsupported hostname normalization mode "{}".'.format(mode)
        )

    source_hosts = []
    short_occurrences = defaultdict(set)
    fqdn_occurrences = defaultdict(lambda: defaultdict(set))

    for source_index, (_source_name, inventory_data) in enumerate(source_inventories):
        hosts = _inventory_hostnames(inventory_data)
        source_hosts.append(hosts)
        for hostname in hosts:
            lowered = hostname.casefold()
            if "." not in hostname:
                short_occurrences[lowered].add((source_index, hostname))
                continue
            short_key = hostname.split(".", 1)[0].casefold()
            fqdn_occurrences[short_key][lowered].add((source_index, hostname))

    aliases = {}

    for short_key, short_rows in short_occurrences.items():
        fqdn_by_name = fqdn_occurrences.get(short_key, {})
        if len(fqdn_by_name) != 1:
            continue

        fqdn_rows = next(iter(fqdn_by_name.values()))
        short_sources = {row[0] for row in short_rows}
        fqdn_sources = {row[0] for row in fqdn_rows}

        # Normalization is for reconciling different member inventories.
        # If a source itself contains both forms, leave the identity alone.
        if short_sources & fqdn_sources:
            continue

        short_names = sorted({row[1] for row in short_rows}, key=str.casefold)
        fqdn_names = sorted({row[1] for row in fqdn_rows}, key=str.casefold)
        if not short_names or not fqdn_names:
            continue

        canonical = short_names[0] if mode == NORMALIZE_SHORT else fqdn_names[0]
        for _source_index, hostname in short_rows | fqdn_rows:
            aliases[hostname] = canonical

    return aliases


def _rename_inventory_hosts(inventory_data, aliases, source_name):
    """Return a copy of an inventory with mapped hostnames renamed."""

    if not aliases:
        return deepcopy(inventory_data)

    result = deepcopy(inventory_data)
    original_hostvars = result["_meta"]["hostvars"]
    renamed_hostvars = {}

    for hostname, hostvars in original_hostvars.items():
        target = aliases.get(hostname, hostname)
        if target in renamed_hostvars:
            raise CompositeInventoryError(
                'Inventory "{}" contains multiple hosts that normalize to "{}".'
                .format(source_name, target)
            )
        renamed_hostvars[target] = hostvars

    result["_meta"]["hostvars"] = renamed_hostvars

    for group_name, group_data in result.items():
        if group_name == "_meta" or not isinstance(group_data, dict):
            continue
        hosts = group_data.get("hosts")
        if not isinstance(hosts, list):
            continue
        renamed = []
        for hostname in hosts:
            target = aliases.get(hostname, hostname) if isinstance(hostname, str) else hostname
            if target not in renamed:
                renamed.append(target)
        group_data["hosts"] = renamed

    return result


def _append_unique(existing, incoming):
    """
    Append values which are not already present.
    """

    for item in incoming:
        if item not in existing:
            existing.append(
                deepcopy(item)
            )

    return existing


def _merge_value(
    existing,
    incoming,
    *,
    path,
    source_name,
):
    """
    Recursively merge two canonical inventory values.
    """

    if isinstance(existing, dict):
        if not isinstance(incoming, dict):
            raise CompositeInventoryError(
                'Inventory "{}" conflicts at "{}": '
                "one value is an object and the other is not."
                .format(
                    source_name,
                    path,
                )
            )

        for key, incoming_value in incoming.items():
            child_path = (
                "{}.{}".format(path, key)
                if path
                else str(key)
            )

            if key not in existing:
                existing[key] = deepcopy(
                    incoming_value
                )

                continue

            existing[key] = _merge_value(
                existing[key],
                incoming_value,
                path=child_path,
                source_name=source_name,
            )

        return existing

    if isinstance(existing, list):
        if not isinstance(incoming, list):
            raise CompositeInventoryError(
                'Inventory "{}" conflicts at "{}": '
                "one value is a list and the other is not."
                .format(
                    source_name,
                    path,
                )
            )

        return _append_unique(
            existing,
            incoming,
        )

    if existing == incoming:
        return existing

    raise CompositeInventoryError(
        'Inventory "{}" conflicts at "{}": '
        "{!r} does not match {!r}.".format(
            source_name,
            path,
            existing,
            incoming,
        )
    )


def _validate_source(
    source_name,
    inventory_data,
):
    """
    Validate the minimum canonical inventory structure.
    """

    if not isinstance(inventory_data, dict):
        raise CompositeInventoryError(
            'Inventory "{}" did not resolve to an object.'
            .format(source_name)
        )

    meta = inventory_data.get(
        "_meta"
    )

    if not isinstance(meta, dict):
        raise CompositeInventoryError(
            'Inventory "{}" has no _meta object.'
            .format(source_name)
        )

    hostvars = meta.get(
        "hostvars"
    )

    if not isinstance(hostvars, dict):
        raise CompositeInventoryError(
            'Inventory "{}" has no hostvars mapping.'
            .format(source_name)
        )


def _all_group_hosts(inventory_data):
    """
    Return every hostname referenced by group host lists.
    """

    hostnames = set()

    for group_name, group_data in inventory_data.items():
        if group_name == "_meta":
            continue

        if not isinstance(group_data, dict):
            continue

        hosts = group_data.get(
            "hosts",
            [],
        )

        if not isinstance(hosts, list):
            continue

        for hostname in hosts:
            if isinstance(hostname, str):
                hostnames.add(
                    hostname
                )

    return hostnames


def append_domain_to_inventory(
    inventory_data,
    append_domain,
    *,
    inventory_name="Inventory",
):
    """Return a copy with a validated default domain appended to short names.

    This transforms only resolved inventory data. It never modifies a provider
    cache or source definition. Existing qualified names are left untouched,
    and collisions are rejected rather than silently merged.
    """

    domain = normalise_default_domain(append_domain)
    if not domain:
        return deepcopy(inventory_data)

    aliases = {
        hostname: "{}.{}".format(hostname, domain)
        for hostname in _inventory_hostnames(inventory_data)
        if "." not in hostname
    }
    return _rename_inventory_hosts(
        inventory_data,
        aliases,
        str(inventory_name or "Inventory"),
    )


def normalize_source_inventories(
    source_inventories,
    mode=NORMALIZE_NONE,
    append_domain="",
):
    """Return source copies with configured hostname transforms applied."""

    aliases = _normalization_map(source_inventories, mode)
    domain = normalise_default_domain(append_domain)
    normalized = []

    for source_name, inventory_data in source_inventories:
        source_label = str(source_name or "Unnamed inventory")
        transformed = _rename_inventory_hosts(
            inventory_data,
            aliases,
            source_label,
        )

        if domain:
            transformed = append_domain_to_inventory(
                transformed,
                domain,
                inventory_name=source_label,
            )

        normalized.append((source_name, transformed))

    return normalized


def compose_inventories(
    source_inventories,
    *,
    normalize_hostnames=NORMALIZE_NONE,
    append_domain="",
):
    """
    Combine canonical inventory data.

    source_inventories must contain two or more tuples:

        [
            ("Satellite", satellite_data),
            ("Zabbix", zabbix_data),
        ]
    """

    if not isinstance(source_inventories, list):
        raise CompositeInventoryError(
            "Composite inventory sources must be a list."
        )

    if len(source_inventories) < 2:
        raise CompositeInventoryError(
            "A composite inventory requires at least two sources."
        )

    source_inventories = normalize_source_inventories(
        source_inventories,
        normalize_hostnames,
        append_domain,
    )

    combined = {
        "_meta": {
            "hostvars": {},
        },
    }

    for source in source_inventories:
        if (
            not isinstance(source, tuple)
            or len(source) != 2
        ):
            raise CompositeInventoryError(
                "Each composite source must contain a "
                "name and resolved inventory."
            )

        source_name, inventory_data = source

        source_name = str(
            source_name or "Unnamed inventory"
        )

        _validate_source(
            source_name,
            inventory_data,
        )

        incoming_hostvars = (
            inventory_data["_meta"]["hostvars"]
        )

        combined_hostvars = (
            combined["_meta"]["hostvars"]
        )

        for hostname, incoming_vars in (
            incoming_hostvars.items()
        ):
            if not isinstance(incoming_vars, dict):
                raise CompositeInventoryError(
                    'Inventory "{}" has invalid variables '
                    'for host "{}".'.format(
                        source_name,
                        hostname,
                    )
                )

            if hostname not in combined_hostvars:
                combined_hostvars[hostname] = (
                    deepcopy(incoming_vars)
                )

                continue

            combined_hostvars[hostname] = (
                _merge_value(
                    combined_hostvars[hostname],
                    incoming_vars,
                    path="_meta.hostvars.{}".format(
                        hostname
                    ),
                    source_name=source_name,
                )
            )

        for group_name, incoming_group in (
            inventory_data.items()
        ):
            if group_name == "_meta":
                continue

            if not isinstance(incoming_group, dict):
                raise CompositeInventoryError(
                    'Inventory "{}" has invalid group "{}".'
                    .format(
                        source_name,
                        group_name,
                    )
                )

            if group_name not in combined:
                combined[group_name] = deepcopy(
                    incoming_group
                )

                continue

            combined[group_name] = _merge_value(
                combined[group_name],
                incoming_group,
                path=group_name,
                source_name=source_name,
            )

    # Hosts may legitimately have no variables. Ensure every host
    # referenced by a group still appears in _meta.hostvars.
    for hostname in _all_group_hosts(
        combined
    ):
        combined["_meta"]["hostvars"].setdefault(
            hostname,
            {},
        )

    combined.setdefault(
        "all",
        {
            "children": [],
        },
    )

    return combined
