"""Three-state source inventory refresh policy, including nested dependencies."""

from .inventory_resolver import (
    InventoryResolutionError,
    refresh_inventory,
    resolve_inventory,
)

NEVER = "never"
IF_MISSING = "if_missing"
ALWAYS = "always"
VALID_POLICIES = frozenset((NEVER, IF_MISSING, ALWAYS))


def validate_policy(value):
    if value not in VALID_POLICIES:
        raise ValueError("Invalid inventory refresh policy: {!r}".format(value))
    return value


def resolve_with_policy(inventory, policy, *, bindings=None, refreshed=None):
    """Use caches, refresh missing source caches, or refresh everything.

    `refreshed` is shared across inventories in a preview to avoid repeating
    external provider requests. Derived inventories are always recalculated.
    """
    validate_policy(policy)
    if policy == ALWAYS:
        return refresh_inventory(
            inventory, bindings=bindings, refreshed_inventory_data=refreshed,
        )
    if policy == NEVER:
        return resolve_inventory(inventory, bindings=bindings)

    # The cache resolver explicitly reports missing caches, but a simple
    # try/except around it would refresh *all* sources. Walk the dependency
    # tree instead so only missing provider caches contact external services.
    from .inventory_resolver import (
        _resolve_static_inventory_live,
        _filtered_source_inventory, _composite_source_inventories,
        _apply_filtered_inventory, _apply_composite_inventory,
        _apply_inventory_output_transforms, _visited_inventory_ids,
    )
    from .inventory_cache import InventoryCacheMissingError, load_inventory_cache

    if refreshed is None:
        refreshed = {}

    def walk(node, visited):
        if node.id in refreshed:
            return refreshed[node.id]
        path = _visited_inventory_ids(node, visited)
        if node.inventory_type in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}:
            try:
                data = load_inventory_cache(node)
            except InventoryCacheMissingError:
                # refresh_inventory also applies output transforms and caches
                # the resulting data for the duration of this operation.
                return refresh_inventory(node, bindings=bindings,
                                         refreshed_inventory_data=refreshed)
            except Exception as exc:
                raise InventoryResolutionError(str(exc)) from exc
        elif node.inventory_type == "static":
            data = _resolve_static_inventory_live(node)
        elif node.inventory_type == "filtered":
            data = _apply_filtered_inventory(
                node, walk(_filtered_source_inventory(node), path), bindings=bindings,
            )
        elif node.inventory_type == "composite":
            sources = _composite_source_inventories(node)
            data = _apply_composite_inventory(
                node, [(src.name, walk(src, path)) for src in sources],
            )
        else:
            raise InventoryResolutionError(
                'Unsupported inventory type "{}".'.format(node.inventory_type)
            )
        result = _apply_inventory_output_transforms(node, data)
        refreshed[node.id] = result
        return result

    return walk(inventory, None)
