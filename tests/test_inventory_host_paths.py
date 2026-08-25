from app.services.inventory_host_paths import observed_host_variable_paths


def test_observed_host_variable_paths_counts_coverage_and_nested_mappings():
    data = {
        "_meta": {"hostvars": {
            "a": {"os": "Windows", "zabbix": {"icmp": {"reachable": True}}},
            "b": {"os": "Linux", "zabbix": {"icmp": {"reachable": False}, "site": "B"}},
        }}
    }
    result = observed_host_variable_paths(data)
    paths = {item["path"]: item["hosts"] for item in result["paths"]}
    assert result["host_count"] == 2
    assert paths["os"] == 2
    assert paths["zabbix.icmp.reachable"] == 2
    assert paths["zabbix.site"] == 1


def test_observed_host_variable_paths_does_not_invent_list_index_paths():
    data = {"_meta": {"hostvars": {"sw": {"ports": [{"name": "Gi1/0/1"}]}}}}
    result = observed_host_variable_paths(data)
    paths = {item["path"] for item in result["paths"]}
    assert "ports" in paths
    assert not any("[]" in path for path in paths)
