from app.services.filtered_inventory import filter_inventory


def _inventory():
    return {
        "_meta": {
            "hostvars": {
                "satellite-host": {
                    "role": "app",
                    "site": "dev04",
                    "foreman_params": {
                        "is_runner_test": True,
                    },
                },
                "zabbix-host": {
                    "role": "db",
                    "site": "dev04",
                    "zabbix": {
                        "tags_by_name": {
                            "journeyman": "yes",
                        },
                    },
                },
                "static-host": {
                    "role": "app",
                    "site": "prod01",
                    "custom": {
                        "enabled": True,
                    },
                },
                "excluded-host": {
                    "role": "app",
                    "site": "dev04",
                    "maintenance": True,
                    "protected": True,
                },
            },
        },
        "all": {
            "children": ["ungrouped"],
        },
        "ungrouped": {
            "hosts": [
                "satellite-host",
                "zabbix-host",
                "static-host",
                "excluded-host",
            ],
        },
    }


def _hosts(filtered):
    return set(filtered["_meta"]["hostvars"])


def test_include_groups_are_or_and_rules_inside_all_group_are_and():
    filtered = filter_inventory(
        _inventory(),
        include_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "variable",
                        "parameter": "role",
                        "operator": "equals",
                        "value": "app",
                    },
                    {
                        "field": "variable",
                        "parameter": "site",
                        "operator": "equals",
                        "value": "dev04",
                    },
                ],
            },
            {
                "match": "all",
                "rules": [
                    {
                        "field": "name",
                        "parameter": "",
                        "operator": "equals",
                        "value": "static-host",
                    },
                ],
            },
        ],
    )

    assert _hosts(filtered) == {
        "satellite-host",
        "static-host",
        "excluded-host",
    }


def test_any_group_matches_when_any_rule_matches():
    filtered = filter_inventory(
        _inventory(),
        include_groups=[
            {
                "match": "any",
                "rules": [
                    {
                        "field": "variable",
                        "parameter": "role",
                        "operator": "equals",
                        "value": "db",
                    },
                    {
                        "field": "variable",
                        "parameter": "custom.enabled",
                        "operator": "equals",
                        "value": "true",
                    },
                ],
            },
        ],
    )

    assert _hosts(filtered) == {
        "zabbix-host",
        "static-host",
    }


def test_exclude_group_can_require_all_rules():
    filtered = filter_inventory(
        _inventory(),
        exclude_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "variable",
                        "parameter": "maintenance",
                        "operator": "equals",
                        "value": "true",
                    },
                    {
                        "field": "variable",
                        "parameter": "protected",
                        "operator": "equals",
                        "value": "true",
                    },
                ],
            },
        ],
    )

    assert "excluded-host" not in _hosts(filtered)
    assert len(_hosts(filtered)) == 3


def test_generic_variable_path_is_inventory_source_agnostic():
    filtered = filter_inventory(
        _inventory(),
        include_groups=[
            {
                "match": "any",
                "rules": [
                    {
                        "field": "variable",
                        "parameter": "foreman_params.is_runner_test",
                        "operator": "exists",
                        "value": "",
                    },
                    {
                        "field": "variable",
                        "parameter": "zabbix.tags_by_name.journeyman",
                        "operator": "equals",
                        "value": "yes",
                    },
                    {
                        "field": "variable",
                        "parameter": "custom.enabled",
                        "operator": "equals",
                        "value": "true",
                    },
                ],
            },
        ],
    )

    assert _hosts(filtered) == {
        "satellite-host",
        "zabbix-host",
        "static-host",
    }


def test_legacy_flat_rules_keep_existing_semantics():
    filtered = filter_inventory(
        _inventory(),
        include_rules=[
            {
                "field": "variable",
                "parameter": "role",
                "operator": "equals",
                "value": "app",
            },
            {
                "field": "variable",
                "parameter": "site",
                "operator": "equals",
                "value": "dev04",
            },
        ],
        exclude_rules=[
            {
                "field": "variable",
                "parameter": "maintenance",
                "operator": "exists",
                "value": "",
            },
            {
                "field": "name",
                "parameter": "",
                "operator": "equals",
                "value": "does-not-exist",
            },
        ],
    )

    # Legacy include rules were ALL; legacy exclude rules were ANY.
    assert _hosts(filtered) == {"satellite-host"}


def test_group_rule_excludes_direct_group_members():
    inventory = _inventory()
    inventory["manual_patch_exclusions"] = {
        "hosts": ["satellite-host", "excluded-host"],
    }

    filtered = filter_inventory(
        inventory,
        exclude_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "group",
                        "parameter": "",
                        "operator": "equals",
                        "value": "manual_patch_exclusions",
                    },
                ],
            },
        ],
    )

    assert _hosts(filtered) == {"zabbix-host", "static-host"}


def test_group_rule_honours_inherited_parent_membership():
    inventory = _inventory()
    inventory["rhv_h"] = {"hosts": ["satellite-host"]}
    inventory["manual_patch_exclusions"] = {
        "children": ["rhv_h"],
    }

    filtered = filter_inventory(
        inventory,
        exclude_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "group",
                        "parameter": "",
                        "operator": "equals",
                        "value": "manual_patch_exclusions",
                    },
                ],
            },
        ],
    )

    assert "satellite-host" not in _hosts(filtered)
    assert len(_hosts(filtered)) == 3


def test_group_rule_can_match_collection_name_with_wildcard():
    inventory = _inventory()
    inventory["manual_patch_exclusions"] = {
        "hosts": ["satellite-host"],
    }

    filtered = filter_inventory(
        inventory,
        include_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "group",
                        "parameter": "",
                        "operator": "glob",
                        "value": "manual_patch_*",
                    },
                ],
            },
        ],
    )

    assert _hosts(filtered) == {"satellite-host"}


def test_satellite_host_collection_can_be_matched_by_display_name():
    inventory = _inventory()
    inventory["foreman_hostcollection_manual_patch_exclusions"] = {
        "hosts": ["satellite-host"]
    }

    filtered = filter_inventory(
        inventory,
        exclude_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "group",
                        "operator": "equals",
                        "value": "manual_patch_exclusions",
                    }
                ],
            }
        ],
    )

    assert "satellite-host" not in _hosts(filtered)
    assert len(_hosts(filtered)) == 3


def test_satellite_generated_host_collection_group_name_still_matches():
    inventory = _inventory()
    inventory["foreman_hostcollection_manual_patch_exclusions"] = {
        "hosts": ["satellite-host"]
    }

    filtered = filter_inventory(
        inventory,
        exclude_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "group",
                        "operator": "equals",
                        "value": "foreman_hostcollection_manual_patch_exclusions",
                    }
                ],
            }
        ],
    )

    assert "satellite-host" not in _hosts(filtered)
    assert len(_hosts(filtered)) == 3


def test_ip_address_field_uses_satellite_foreman_ipv4():
    inventory = _inventory()
    inventory["_meta"]["hostvars"]["satellite-host"]["foreman"] = {
        "ipv4": "192.0.2.10",
    }
    inventory["_meta"]["hostvars"]["zabbix-host"]["ansible_host"] = (
        "198.51.100.10"
    )

    filtered = filter_inventory(
        inventory,
        include_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "ansible_host",
                        "operator": "starts_with",
                        "value": "192.0.2",
                    }
                ],
            }
        ],
    )

    assert _hosts(filtered) == {"satellite-host"}


def test_ip_address_field_falls_back_to_satellite_network_fact():
    inventory = _inventory()
    inventory["_meta"]["hostvars"]["satellite-host"]["foreman_facts"] = {
        "network::ipv4_address": "192.0.2.11",
    }

    filtered = filter_inventory(
        inventory,
        include_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "ansible_host",
                        "operator": "starts_with",
                        "value": "192.0.2",
                    }
                ],
            }
        ],
    )

    assert _hosts(filtered) == {"satellite-host"}


def test_ip_address_field_prefers_explicit_ansible_host():
    inventory = _inventory()
    variables = inventory["_meta"]["hostvars"]["satellite-host"]
    variables["ansible_host"] = "10.0.0.10"
    variables["foreman"] = {"ipv4": "192.0.2.10"}

    filtered = filter_inventory(
        inventory,
        include_groups=[
            {
                "match": "all",
                "rules": [
                    {
                        "field": "ansible_host",
                        "operator": "starts_with",
                        "value": "10.0.0",
                    }
                ],
            }
        ],
    )

    assert _hosts(filtered) == {"satellite-host"}
