import json
from types import SimpleNamespace

import pytest

from app.services.inventory_dependencies import (
    InventoryDependencyError,
    composite_member_leaf_sources,
    validate_composite_source_lineages,
)


def _inventory(inventory_id, name, inventory_type, config=None):
    return SimpleNamespace(
        id=inventory_id,
        name=name,
        inventory_type=inventory_type,
        config_json=json.dumps(config or {}),
    )


def test_composite_lineage_rejects_direct_source_and_filtered_derivative():
    satellite = _inventory(1, "Satellite", "satellite")
    filtered = _inventory(
        2,
        "Satellite safe",
        "filtered",
        {"source_inventory_id": 1},
    )

    with pytest.raises(InventoryDependencyError) as exc_info:
        validate_composite_source_lineages(
            [1, 2],
            inventories=[satellite, filtered],
        )

    message = str(exc_info.value)
    assert "lineage conflict" in message
    assert '"Satellite"' in message
    assert '"Satellite safe"' in message
    assert 'source inventory "Satellite"' in message


def test_composite_lineage_rejects_sibling_filtered_branches():
    satellite = _inventory(1, "Satellite", "satellite")
    filtered_a = _inventory(
        2,
        "Linux hosts",
        "filtered",
        {"source_inventory_id": 1},
    )
    filtered_b = _inventory(
        3,
        "Safe Linux hosts",
        "filtered",
        {"source_inventory_id": 1},
    )

    with pytest.raises(InventoryDependencyError):
        validate_composite_source_lineages(
            [2, 3],
            inventories=[satellite, filtered_a, filtered_b],
        )


def test_composite_lineage_checks_nested_composites_all_the_way_down():
    satellite = _inventory(1, "Satellite", "satellite")
    zabbix = _inventory(2, "Zabbix", "zabbix")
    netbox = _inventory(3, "NetBox", "netbox")
    filtered_satellite = _inventory(
        4,
        "Satellite filtered",
        "filtered",
        {"source_inventory_id": 1},
    )
    nested_composite = _inventory(
        5,
        "Satellite plus Zabbix",
        "composite",
        {"source_inventory_ids": [4, 2]},
    )
    filtered_nested = _inventory(
        6,
        "Filtered nested composite",
        "filtered",
        {"source_inventory_id": 5},
    )

    inventories = [
        satellite,
        zabbix,
        netbox,
        filtered_satellite,
        nested_composite,
        filtered_nested,
    ]

    leaves = composite_member_leaf_sources(
        [6, 3],
        inventories=inventories,
    )
    assert leaves[6] == {1, 2}
    assert leaves[3] == {3}

    assert validate_composite_source_lineages(
        [6, 3],
        inventories=inventories,
    ) == [6, 3]

    with pytest.raises(InventoryDependencyError):
        validate_composite_source_lineages(
            [6, 1],
            inventories=inventories,
        )


def test_composite_lineage_rejects_dependency_cycle_defensively():
    composite_a = _inventory(
        1,
        "Composite A",
        "composite",
        {"source_inventory_ids": [2]},
    )
    filtered_b = _inventory(
        2,
        "Filtered B",
        "filtered",
        {"source_inventory_id": 1},
    )
    static = _inventory(3, "Static", "static")

    with pytest.raises(InventoryDependencyError) as exc_info:
        validate_composite_source_lineages(
            [1, 3],
            inventories=[composite_a, filtered_b, static],
        )

    assert "dependency cycle detected" in str(exc_info.value)
