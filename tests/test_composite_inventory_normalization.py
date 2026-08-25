from app.services.composite_inventory import (
    CompositeInventoryError,
    append_domain_to_inventory,
    compose_inventories,
)


def _inventory(hostname, variables=None, group="all"):
    return {
        "_meta": {"hostvars": {hostname: variables or {}}},
        group: {"hosts": [hostname]},
    }


def test_composite_normalizes_unambiguous_pair_to_short_name():
    satellite = _inventory("rhel02", {"satellite_id": 10}, "satellite")
    zabbix = _inventory(
        "rhel02.example.com",
        {"zabbix_id": "20"},
        "zabbix",
    )

    result = compose_inventories(
        [("Satellite", satellite), ("Zabbix", zabbix)],
        normalize_hostnames="short",
    )

    assert set(result["_meta"]["hostvars"]) == {"rhel02"}
    assert result["_meta"]["hostvars"]["rhel02"] == {
        "satellite_id": 10,
        "zabbix_id": "20",
    }
    assert result["satellite"]["hosts"] == ["rhel02"]
    assert result["zabbix"]["hosts"] == ["rhel02"]


def test_composite_normalizes_unambiguous_pair_to_fqdn():
    short = _inventory("rhel", {"source": {"satellite": True}})
    fqdn = _inventory("rhel.example.com", {"source": {"zabbix": True}})

    result = compose_inventories(
        [("Satellite", short), ("Zabbix", fqdn)],
        normalize_hostnames="fqdn",
    )

    assert set(result["_meta"]["hostvars"]) == {"rhel.example.com"}
    assert result["_meta"]["hostvars"]["rhel.example.com"] == {
        "source": {"satellite": True, "zabbix": True},
    }


def test_composite_does_not_normalize_ambiguous_fqdns():
    short = _inventory("rhel", {"short": True})
    fqdn_one = _inventory("rhel.example.com", {"domain": "example"})
    fqdn_two = _inventory("rhel.ot.example.com", {"domain": "ot"})

    result = compose_inventories(
        [
            ("Satellite", short),
            ("Zabbix", fqdn_one),
            ("NetBox", fqdn_two),
        ],
        normalize_hostnames="short",
    )

    assert set(result["_meta"]["hostvars"]) == {
        "rhel",
        "rhel.example.com",
        "rhel.ot.example.com",
    }


def test_composite_fqdn_mode_does_not_invent_domain():
    one = _inventory("rhel", {"a": True})
    two = _inventory("other", {"b": True})

    result = compose_inventories(
        [("One", one), ("Two", two)],
        normalize_hostnames="fqdn",
    )

    assert set(result["_meta"]["hostvars"]) == {"rhel", "other"}


def test_composite_normalization_only_reconciles_different_sources():
    first = {
        "_meta": {
            "hostvars": {
                "rhel": {"short": True},
                "rhel.example.com": {"fqdn": True},
            }
        },
        "all": {"hosts": ["rhel", "rhel.example.com"]},
    }
    second = _inventory("other.example.com", {"other": True})

    result = compose_inventories(
        [("First", first), ("Second", second)],
        normalize_hostnames="short",
    )

    assert "rhel" in result["_meta"]["hostvars"]
    assert "rhel.example.com" in result["_meta"]["hostvars"]


def test_composite_appends_default_domain_to_short_names_without_changing_fqdns():
    satellite = _inventory("rhel02", {"satellite": True}, "satellite")
    zabbix = _inventory(
        "benapp01.other.example.com",
        {"zabbix": True},
        "zabbix",
    )

    result = compose_inventories(
        [("Satellite", satellite), ("Zabbix", zabbix)],
        append_domain="example.com",
    )

    assert set(result["_meta"]["hostvars"]) == {
        "rhel02.example.com",
        "benapp01.other.example.com",
    }
    assert result["satellite"]["hosts"] == [
        "rhel02.example.com"
    ]
    assert result["zabbix"]["hosts"] == ["benapp01.other.example.com"]


def test_composite_append_domain_can_explicitly_merge_short_name_with_matching_fqdn():
    short = _inventory("rhel", {"satellite": True}, "satellite")
    fqdn = _inventory("rhel.example.com", {"zabbix": True}, "zabbix")

    result = compose_inventories(
        [("Satellite", short), ("Zabbix", fqdn)],
        append_domain="example.com",
    )

    assert set(result["_meta"]["hostvars"]) == {"rhel.example.com"}
    assert result["_meta"]["hostvars"]["rhel.example.com"] == {
        "satellite": True,
        "zabbix": True,
    }


def test_composite_append_domain_does_not_modify_source_inventory_data():
    satellite = _inventory("rhel", {"satellite": True}, "satellite")
    zabbix = _inventory("other.example.com", {"zabbix": True}, "zabbix")

    compose_inventories(
        [("Satellite", satellite), ("Zabbix", zabbix)],
        append_domain="example.com",
    )

    assert set(satellite["_meta"]["hostvars"]) == {"rhel"}
    assert satellite["satellite"]["hosts"] == ["rhel"]


def test_composite_append_domain_rejects_invalid_domain():
    first = _inventory("rhel")
    second = _inventory("other")

    import pytest
    from app.services.composite_inventory import CompositeInventoryError

    with pytest.raises(CompositeInventoryError, match="valid DNS domain"):
        compose_inventories(
            [("One", first), ("Two", second)],
            append_domain="not_a_domain",
        )


def test_append_domain_transform_is_independent_of_composite_inventory():
    source = _inventory("switch15", {"zabbix_id": "15"}, "zabbix")

    result = append_domain_to_inventory(
        source,
        "example.com",
        inventory_name="Zabbix Journeyman Hosts",
    )

    assert set(result["_meta"]["hostvars"]) == {
        "switch15.example.com"
    }
    assert result["zabbix"]["hosts"] == [
        "switch15.example.com"
    ]
    assert set(source["_meta"]["hostvars"]) == {"switch15"}
    assert source["zabbix"]["hosts"] == ["switch15"]


def test_append_domain_transform_rejects_existing_fqdn_collision():
    source = {
        "_meta": {
            "hostvars": {
                "rhel": {"short": True},
                "rhel.example.com": {"fqdn": True},
            }
        },
        "all": {"hosts": ["rhel", "rhel.example.com"]},
    }

    import pytest

    with pytest.raises(CompositeInventoryError, match="multiple hosts"):
        append_domain_to_inventory(
            source,
            "example.com",
            inventory_name="Example",
        )
