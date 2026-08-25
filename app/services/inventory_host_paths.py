"""Discover host-variable paths observed in canonical inventory data.

The result is advisory UI metadata only.  Filter validation deliberately does
not require a path to be observed because inventories may be heterogeneous or
stale and a legitimate path may exist only after the next refresh.
"""

from collections import Counter


def _paths_from_value(value, prefix="", *, depth=0, max_depth=12):
    if not prefix or depth > max_depth:
        return set()

    paths = {prefix}
    if isinstance(value, dict) and depth < max_depth:
        for key, child in value.items():
            key = str(key or "").strip()
            if not key or "." in key:
                # Dot-delimited filter paths cannot address mapping keys that
                # themselves contain dots, so do not advertise such paths.
                continue
            child_prefix = "{}.{}".format(prefix, key)
            paths.update(
                _paths_from_value(
                    child,
                    child_prefix,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )
    return paths


def observed_host_variable_paths(inventory_data, *, max_paths=2000):
    """Return observed dotted hostvar paths with per-host coverage counts."""

    hostvars = (
        inventory_data.get("_meta", {}).get("hostvars", {})
        if isinstance(inventory_data, dict)
        else {}
    )
    if not isinstance(hostvars, dict):
        hostvars = {}

    counts = Counter()
    for variables in hostvars.values():
        if not isinstance(variables, dict):
            continue
        host_paths = set()
        for key, value in variables.items():
            key = str(key or "").strip()
            if not key or "." in key:
                continue
            host_paths.update(_paths_from_value(value, key))
        counts.update(host_paths)

    # Deterministic alphabetical ordering makes the browser's datalist useful
    # while the cap prevents unexpectedly huge provider schemas from bloating
    # the response.
    paths = [
        {"path": path, "hosts": counts[path]}
        for path in sorted(counts)[:max_paths]
    ]
    return {
        "host_count": len(hostvars),
        "paths": paths,
        "truncated": len(counts) > max_paths,
    }
