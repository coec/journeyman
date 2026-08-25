"""
Filter canonical Ansible inventory data.

Filtered inventories retain the native host variables and group
structure produced by the source inventory while removing hosts that
do not satisfy the configured rules.
"""

from copy import deepcopy
from fnmatch import fnmatchcase


SUPPORTED_FILTER_OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "glob",
        "contains",
        "starts_with",
        "ends_with",
        "exists",
        "not_exists",
    }
)

_MISSING = object()


class FilteredInventoryError(Exception):
    """
    Raised when a filtered inventory cannot be resolved.
    """


def _path_value(
    hostname,
    host_variables,
    field,
    *,
    parameter=None,
    host_groups=None,
):
    """
    Read a value from host variables.

    ``name`` represents the inventory hostname.

    ``variable`` retrieves one named dotted path from host variables.

    ``foreman_param`` remains as a convenience shortcut for one named value
    from the ``foreman_params`` namespace.
    """

    if field == "name":
        return hostname

    if field == "ansible_host":
        # Treat the UI's "IP address / ansible_host" field as a semantic
        # address rather than requiring every inventory provider to populate
        # the same hostvar.  Static/Zabbix inventories normally expose
        # ansible_host directly, while the Foreman inventory plugin carries
        # Satellite addresses in foreman.ipv4 (with the gathered Foreman fact
        # as a final fallback).
        ansible_host = host_variables.get("ansible_host")
        if ansible_host not in (None, ""):
            return ansible_host

        foreman = host_variables.get("foreman", {})
        if not isinstance(foreman, dict):
            foreman = {}

        ipv4 = foreman.get("ipv4")
        if ipv4 not in (None, ""):
            return ipv4

        facts = host_variables.get("foreman_facts", {})
        if not isinstance(facts, dict):
            facts = {}

        ipv4 = facts.get("network::ipv4_address")
        if ipv4 not in (None, ""):
            return ipv4

        return _MISSING

    if field == "group":
        groups = set(host_groups or ())

        # The Foreman inventory plugin exposes Satellite host collections
        # as Ansible groups named
        # ``foreman_hostcollection_<collection>`` when its default
        # ``group_prefix`` is used.  Filter rules should accept the
        # Satellite host-collection name shown to an administrator rather
        # than requiring knowledge of the plugin's generated group name.
        # Keep the real Ansible group names too, so ordinary group rules
        # and explicit generated-name rules continue to work.
        for group_name in tuple(groups):
            for prefix in (
                "foreman_hostcollection_",
                "hostcollection_",
            ):
                if group_name.startswith(prefix):
                    collection_name = group_name[len(prefix):]
                    if collection_name:
                        groups.add(collection_name)
                    break

        return sorted(groups)

    if field == "variable":
        field = str(parameter or "").strip()
        if not field:
            return _MISSING

    if field == "foreman_param":
        parameters = host_variables.get(
            "foreman_params",
            {},
        )

        if not isinstance(parameters, dict):
            return _MISSING

        parameter = str(
            parameter or ""
        ).strip()

        if (
            not parameter
            or parameter not in parameters
        ):
            return _MISSING

        return parameters[parameter]

    current = host_variables

    for component in field.split("."):
        if (
            not isinstance(current, dict)
            or component not in current
        ):
            return _MISSING

        current = current[component]

    return current


def _candidate_values(value):
    """
    Return scalar values suitable for rule comparison.
    """

    if isinstance(value, (list, tuple, set)):
        return list(value)

    return [value]


def _comparison_value(value):
    """
    Normalize a value for case-insensitive comparison.
    """

    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value).strip().casefold()


def _rule_matches(
    hostname,
    host_variables,
    rule,
    *,
    host_groups=None,
):
    """
    Return whether one host matches one filter rule.
    """

    field = str(
        rule.get("field") or ""
    ).strip()

    operator = str(
        rule.get("operator") or ""
    ).strip()

    expected = _comparison_value(
        rule.get("value")
    )

    parameter = str(
        rule.get("parameter") or ""
    ).strip()

    actual = _path_value(
        hostname,
        host_variables,
        field,
        parameter=parameter,
        host_groups=host_groups,
    )

    if operator == "exists":
        return actual is not _MISSING

    if operator == "not_exists":
        return actual is _MISSING

    # Missing values do not match ordinary comparisons. Use
    # not_exists when the absence of a value is significant.
    if actual is _MISSING:
        return False

    candidates = [
        _comparison_value(value)
        for value in _candidate_values(actual)
    ]

    if operator == "equals":
        return any(
            value == expected
            for value in candidates
        )

    if operator == "not_equals":
        return all(
            value != expected
            for value in candidates
        )

    if operator == "glob":
        return any(
            fnmatchcase(value, expected)
            for value in candidates
        )

    if operator == "contains":
        return any(
            expected in value
            for value in candidates
        )

    if operator == "starts_with":
        return any(
            value.startswith(expected)
            for value in candidates
        )

    if operator == "ends_with":
        return any(
            value.endswith(expected)
            for value in candidates
        )

    raise FilteredInventoryError(
        'Unsupported filter operator "{}".'
        .format(operator)
    )


def _validate_rules(
    rules,
    *,
    rule_set_name,
):
    """
    Validate rule structures loaded from inventory configuration.
    """

    if rules is None:
        return []

    if not isinstance(rules, list):
        raise FilteredInventoryError(
            "{} rules must be a list.".format(
                rule_set_name
            )
        )

    validated = []

    for index, rule in enumerate(
        rules,
        start=1,
    ):
        if not isinstance(rule, dict):
            raise FilteredInventoryError(
                "{} rule {} must be an object.".format(
                    rule_set_name,
                    index,
                )
            )

        field = str(
            rule.get("field") or ""
        ).strip()

        parameter = str(
            rule.get("parameter") or ""
        ).strip()

        operator = str(
            rule.get("operator") or ""
        ).strip()

        value = rule.get("value")

        if not field:
            raise FilteredInventoryError(
                "{} rule {} has no field.".format(
                    rule_set_name,
                    index,
                )
            )

        if operator not in SUPPORTED_FILTER_OPERATORS:
            raise FilteredInventoryError(
                "{} rule {} has an invalid operator.".format(
                    rule_set_name,
                    index,
                )
            )

        if (
            operator not in {"exists", "not_exists"}
            and str(value or "").strip() == ""
        ):
            raise FilteredInventoryError(
                "{} rule {} has no comparison value.".format(
                    rule_set_name,
                    index,
                )
            )

        if (
            field in {"foreman_param", "variable"}
            and not parameter
        ):
            description = (
                "Satellite host parameter name"
                if field == "foreman_param"
                else "host variable path"
            )
            raise FilteredInventoryError(
                "{} rule {} has no {}.".format(
                    rule_set_name,
                    index,
                    description,
                )
            )

        validated.append(
            {
                "field": field,
                "parameter": parameter,
                "operator": operator,
                "value": value,
            }
        )

    return validated


SUPPORTED_GROUP_MATCH_MODES = frozenset({"all", "any"})


def _legacy_group(rules, *, match):
    """Return one backward-compatible group for a legacy flat rule list."""

    validated = _validate_rules(
        rules,
        rule_set_name="Filter",
    )

    if not validated:
        return []

    return [{"match": match, "rules": validated}]


def _validate_groups(
    groups,
    *,
    group_set_name,
    legacy_rules=None,
    legacy_match="all",
):
    """Validate grouped filter rules, with transparent legacy-list support."""

    if groups is None:
        return _legacy_group(
            legacy_rules,
            match=legacy_match,
        )

    if not isinstance(groups, list):
        raise FilteredInventoryError(
            "{} groups must be a list.".format(group_set_name)
        )

    validated = []

    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise FilteredInventoryError(
                "{} group {} must be an object.".format(
                    group_set_name,
                    index,
                )
            )

        match = str(group.get("match") or "all").strip().lower()
        if match not in SUPPORTED_GROUP_MATCH_MODES:
            raise FilteredInventoryError(
                "{} group {} has an invalid match mode.".format(
                    group_set_name,
                    index,
                )
            )

        rules = _validate_rules(
            group.get("rules"),
            rule_set_name="{} group {}".format(
                group_set_name,
                index,
            ),
        )

        # Empty groups have no filtering effect and are ignored.
        if not rules:
            continue

        validated.append({"match": match, "rules": rules})

    return validated


def _group_matches(
    hostname,
    host_variables,
    group,
    *,
    host_groups=None,
):
    """Return whether a host satisfies one ALL/ANY rule group."""

    matches = (
        _rule_matches(
            hostname,
            host_variables,
            rule,
            host_groups=host_groups,
        )
        for rule in group["rules"]
    )

    if group["match"] == "all":
        return all(matches)

    return any(matches)


def _host_group_memberships(inventory_data, hostnames):
    """Return every Ansible group/collection that contains each host.

    Membership includes inherited parent groups. If ``parent`` has
    ``children: [child]`` and a host belongs to ``child``, it also belongs
    to ``parent`` for filtering purposes, matching normal Ansible inventory
    semantics. Cycles are tolerated defensively and do not recurse forever.
    """

    group_data = {
        name: data
        for name, data in inventory_data.items()
        if name != "_meta" and isinstance(data, dict)
    }

    cache = {}

    def members(group_name, trail=None):
        if group_name in cache:
            return cache[group_name]

        trail = set(trail or ())
        if group_name in trail:
            return set()

        trail.add(group_name)
        data = group_data.get(group_name, {})
        result = set(
            host
            for host in data.get("hosts", [])
            if host in hostnames
        ) if isinstance(data.get("hosts"), list) else set()

        children = data.get("children", [])
        if isinstance(children, list):
            for child in children:
                result.update(members(child, trail))

        cache[group_name] = result
        return result

    memberships = {hostname: set() for hostname in hostnames}

    for group_name in group_data:
        for hostname in members(group_name):
            memberships[hostname].add(group_name)

    return memberships


def filter_inventory(
    inventory_data,
    *,
    include_groups=None,
    exclude_groups=None,
    include_rules=None,
    exclude_rules=None,
):
    """
    Filter canonical ``ansible-inventory --list`` data.

    A host is included when it matches any include group. Within a group,
    rules may be combined with either ALL or ANY semantics. A host is removed
    when it matches any exclude group.

    With no include groups, all hosts are initially included. Legacy flat
    ``include`` and ``exclude`` rule lists remain supported transparently.
    """

    if not isinstance(inventory_data, dict):
        raise FilteredInventoryError(
            "The source inventory result is not an object."
        )

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars")
    )

    if not isinstance(hostvars, dict):
        raise FilteredInventoryError(
            "The source inventory has no hostvars mapping."
        )

    include_groups = _validate_groups(
        include_groups,
        group_set_name="Include",
        legacy_rules=include_rules,
        legacy_match="all",
    )

    exclude_groups = _validate_groups(
        exclude_groups,
        group_set_name="Exclude",
        legacy_rules=exclude_rules,
        legacy_match="any",
    )

    host_group_memberships = _host_group_memberships(
        inventory_data,
        set(hostvars),
    )

    retained_hosts = set()

    for hostname, variables in hostvars.items():
        if not isinstance(variables, dict):
            variables = {}

        included = (
            not include_groups
            or any(
                _group_matches(
                    hostname,
                    variables,
                    group,
                    host_groups=host_group_memberships.get(
                        hostname,
                        set(),
                    ),
                )
                for group in include_groups
            )
        )

        if not included:
            continue

        excluded = any(
            _group_matches(
                hostname,
                variables,
                group,
                host_groups=host_group_memberships.get(
                    hostname,
                    set(),
                ),
            )
            for group in exclude_groups
        )

        if not excluded:
            retained_hosts.add(hostname)

    filtered = deepcopy(
        inventory_data
    )

    filtered["_meta"]["hostvars"] = {
        hostname: variables
        for hostname, variables in hostvars.items()
        if hostname in retained_hosts
    }

    # Preserve group definitions and variables, but remove filtered
    # hosts from every group's host list.
    for group_name, group_data in filtered.items():
        if group_name == "_meta":
            continue

        if not isinstance(group_data, dict):
            continue

        group_hosts = group_data.get("hosts")

        if isinstance(group_hosts, list):
            group_data["hosts"] = [
                hostname
                for hostname in group_hosts
                if hostname in retained_hosts
            ]

    return filtered
