"""
Inspect dependencies between Journeyman inventory definitions.

Source inventories have no dependencies. Derived inventories such as
filtered and composite inventories depend on one or more other
inventories.
"""

from app.services.name_ordering import reserved_name_ordering

import json

from app.models.inventory import Inventory


class InventoryDependencyError(Exception):
    """
    Raised when inventory dependency configuration is invalid.
    """

def _normalise_dependency_ids(
    dependency_ids,
):
    """
    Return unique positive inventory IDs while preserving order.
    """

    normalised = []
    seen = set()

    for dependency_id in dependency_ids:
        try:
            dependency_id = int(
                dependency_id
            )

        except (TypeError, ValueError) as exc:
            raise InventoryDependencyError(
                "Inventory dependency IDs must be integers."
            ) from exc

        if dependency_id < 1:
            raise InventoryDependencyError(
                "Inventory dependency IDs must be positive."
            )

        if dependency_id in seen:
            continue

        seen.add(
            dependency_id
        )

        normalised.append(
            dependency_id
        )

    return normalised


def _inventory_dependency_graph(
    inventories=None,
):
    """
    Return direct dependencies keyed by inventory ID.
    """

    if inventories is None:
        inventories = Inventory.query.all()

    graph = {}

    for inventory in inventories:
        graph[inventory.id] = (
            inventory_dependency_ids(
                inventory
            )
        )

    return graph


def _find_dependency_path(
    graph,
    start_inventory_id,
    target_inventory_id,
    path=None,
):
    """
    Find a dependency path from start to target.
    """

    if path is None:
        path = []

    if start_inventory_id in path:
        return None

    current_path = (
        path
        + [start_inventory_id]
    )

    if start_inventory_id == target_inventory_id:
        return current_path

    for dependency_id in graph.get(
        start_inventory_id,
        set(),
    ):
        result = _find_dependency_path(
            graph,
            dependency_id,
            target_inventory_id,
            current_path,
        )

        if result:
            return result

    return None


def validate_inventory_dependency_update(
    inventory_id,
    dependency_ids,
    inventories=None,
):
    """
    Validate proposed dependencies for one inventory.

    The proposed dependencies replace the inventory's current
    dependencies while checking for cycles.
    """

    dependency_ids = _normalise_dependency_ids(
        dependency_ids
    )

    if inventory_id is None:
        return dependency_ids

    inventory_id = int(
        inventory_id
    )

    if inventory_id in dependency_ids:
        raise InventoryDependencyError(
            "An inventory cannot use itself as a source."
        )

    if inventories is None:
        inventories = Inventory.query.all()

    graph = _inventory_dependency_graph(
        inventories
    )

    graph[inventory_id] = set(
        dependency_ids
    )

    names_by_id = {
        inventory.id: inventory.name
        for inventory in inventories
    }

    for dependency_id in dependency_ids:
        dependency_path = _find_dependency_path(
            graph,
            dependency_id,
            inventory_id,
        )

        if not dependency_path:
            continue

        cycle_path = (
            [inventory_id]
            + dependency_path
        )

        cycle_names = [
            names_by_id.get(
                item_id,
                "Inventory {}".format(item_id),
            )
            for item_id in cycle_path
        ]

        raise InventoryDependencyError(
            "Inventory dependency cycle detected: {}."
            .format(
                " → ".join(cycle_names)
            )
        )

    return dependency_ids



def _inventory_name(inventory_id, names_by_id):
    return names_by_id.get(
        inventory_id,
        "Inventory {}".format(inventory_id),
    )


def _leaf_source_inventory_ids(
    inventory_id,
    graph,
    *,
    names_by_id,
    path=None,
):
    """Return ultimate source inventory IDs for one inventory.

    Filtered and Composite inventories are expanded recursively until
    inventories with no inventory dependencies are reached. Cycles are
    rejected defensively even if malformed configuration bypassed normal
    save-time validation.
    """

    if path is None:
        path = []

    if inventory_id in path:
        cycle_ids = path[path.index(inventory_id):] + [inventory_id]
        raise InventoryDependencyError(
            "Inventory dependency cycle detected: {}.".format(
                " → ".join(
                    _inventory_name(item_id, names_by_id)
                    for item_id in cycle_ids
                )
            )
        )

    dependencies = graph.get(inventory_id, set())
    if not dependencies:
        return {inventory_id}

    leaves = set()
    next_path = path + [inventory_id]
    for dependency_id in dependencies:
        leaves.update(
            _leaf_source_inventory_ids(
                dependency_id,
                graph,
                names_by_id=names_by_id,
                path=next_path,
            )
        )

    return leaves


def inventory_leaf_source_ids(inventory_id, inventories=None):
    """Return the recursive leaf/source inventory IDs for one inventory."""

    if inventories is None:
        inventories = Inventory.query.all()

    inventories = list(inventories)
    graph = _inventory_dependency_graph(inventories)
    names_by_id = {inventory.id: inventory.name for inventory in inventories}

    inventory_id = int(inventory_id)
    if inventory_id not in graph:
        raise InventoryDependencyError(
            'Inventory "{}" no longer exists.'.format(
                _inventory_name(inventory_id, names_by_id)
            )
        )

    return _leaf_source_inventory_ids(
        inventory_id,
        graph,
        names_by_id=names_by_id,
    )


def composite_member_leaf_sources(source_inventory_ids, inventories=None):
    """Return recursive leaf/source IDs keyed by Composite member ID."""

    source_inventory_ids = _normalise_dependency_ids(source_inventory_ids)
    if inventories is None:
        inventories = Inventory.query.all()

    inventories = list(inventories)
    graph = _inventory_dependency_graph(inventories)
    names_by_id = {inventory.id: inventory.name for inventory in inventories}

    result = {}
    for source_inventory_id in source_inventory_ids:
        if source_inventory_id not in graph:
            raise InventoryDependencyError(
                'Inventory "{}" no longer exists.'.format(
                    _inventory_name(source_inventory_id, names_by_id)
                )
            )
        result[source_inventory_id] = _leaf_source_inventory_ids(
            source_inventory_id,
            graph,
            names_by_id=names_by_id,
        )

    return result


def validate_composite_source_lineages(source_inventory_ids, inventories=None):
    """Reject Composite members whose recursive source lineages overlap.

    Each selected member must represent an independent inventory lineage.
    A leaf/source inventory may therefore occur beneath only one member, no
    matter how many Filtered or nested Composite layers exist in between.
    """

    source_inventory_ids = _normalise_dependency_ids(source_inventory_ids)
    if inventories is None:
        inventories = Inventory.query.all()

    inventories = list(inventories)
    names_by_id = {inventory.id: inventory.name for inventory in inventories}
    member_leaves = composite_member_leaf_sources(
        source_inventory_ids,
        inventories=inventories,
    )

    seen_leaf_owner = {}
    for member_id in source_inventory_ids:
        for leaf_id in sorted(member_leaves[member_id]):
            previous_member_id = seen_leaf_owner.get(leaf_id)
            if previous_member_id is None:
                seen_leaf_owner[leaf_id] = member_id
                continue

            raise InventoryDependencyError(
                'Composite inventory lineage conflict: "{}" and "{}" '
                'both derive from source inventory "{}". Select only one '
                'branch from each underlying source inventory.'.format(
                    _inventory_name(previous_member_id, names_by_id),
                    _inventory_name(member_id, names_by_id),
                    _inventory_name(leaf_id, names_by_id),
                )
            )

    return source_inventory_ids

def inventory_dependency_ids(inventory):
    """
    Return the direct inventory IDs used by one inventory.

    Filtered inventory configuration:

        {
            "source_inventory_id": 1
        }

    Future composite inventory configuration:

        {
            "source_inventory_ids": [1, 2, 3]
        }
    """

    value = inventory.config_json or "{}"

    try:
        config = json.loads(value)

    except (TypeError, ValueError) as exc:
        raise InventoryDependencyError(
            'Inventory "{}" contains invalid configuration JSON.'
            .format(inventory.name)
        ) from exc

    if not isinstance(config, dict):
        raise InventoryDependencyError(
            'Inventory "{}" configuration must be an object.'
            .format(inventory.name)
        )

    dependency_ids = set()

    if inventory.inventory_type == "filtered":
        source_inventory_id = config.get(
            "source_inventory_id"
        )

        if source_inventory_id is None:
            return dependency_ids

        try:
            dependency_ids.add(
                int(source_inventory_id)
            )

        except (TypeError, ValueError) as exc:
            raise InventoryDependencyError(
                'Filtered inventory "{}" has an invalid '
                "source inventory ID.".format(
                    inventory.name
                )
            ) from exc

    elif inventory.inventory_type == "composite":
        source_inventory_ids = config.get(
            "source_inventory_ids",
            [],
        )

        if source_inventory_ids is None:
            return dependency_ids

        if not isinstance(source_inventory_ids, list):
            raise InventoryDependencyError(
                'Composite inventory "{}" source inventories '
                "must be a list.".format(
                    inventory.name
                )
            )

        for source_inventory_id in source_inventory_ids:
            try:
                dependency_ids.add(
                    int(source_inventory_id)
                )

            except (TypeError, ValueError) as exc:
                raise InventoryDependencyError(
                    'Composite inventory "{}" has an invalid '
                    "source inventory ID.".format(
                        inventory.name
                    )
                ) from exc

    return dependency_ids


def direct_dependants_by_inventory(
    inventories=None,
):
    """
    Return inventories grouped by the direct inventory they depend on.

    Result:

        {
            source_inventory_id: [
                filtered_inventory,
                composite_inventory,
            ]
        }
    """

    if inventories is None:
        inventories = (
            Inventory.query
            .order_by(*reserved_name_ordering(Inventory.name))
            .all()
        )

    dependants_by_inventory = {}

    for inventory in inventories:
        dependency_ids = inventory_dependency_ids(
            inventory
        )

        for dependency_id in dependency_ids:
            dependants_by_inventory.setdefault(
                dependency_id,
                [],
            ).append(
                inventory
            )

    return dependants_by_inventory


def direct_inventory_dependants(
    inventory_id,
):
    """
    Return inventories which directly depend on one inventory.
    """

    return direct_dependants_by_inventory().get(
        inventory_id,
        [],
    )
