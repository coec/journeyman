from app.services.project_package_inputs import prune_stale_reactor_mappings


class FakeReactor:
    def __init__(self, mappings):
        self._mappings = dict(mappings)

    def get_mappings(self):
        return dict(self._mappings)

    def set_mappings(self, mappings):
        self._mappings = dict(mappings)


class FakePackage:
    def __init__(self, reactors):
        self.reactors = list(reactors)


def test_removed_package_input_prunes_stale_reactor_mapping():
    reactor = FakeReactor({
        "device": {"kind": "signal", "path": "fields.zabbix.host_name"},
        "tunnel_interface": {"kind": "signal", "path": "fields.tags.interface"},
        "description": {"kind": "signal", "path": "fields.tags.description"},
    })
    package = FakePackage([reactor])

    changed = prune_stale_reactor_mappings(
        package,
        {"device", "tunnel_interface"},
    )

    assert changed == 1
    assert reactor.get_mappings() == {
        "device": {
            "kind": "signal",
            "path": "fields.zabbix.host_name",
        },
        "tunnel_interface": {
            "kind": "signal",
            "path": "fields.tags.interface",
        },
    }


def test_pruning_is_noop_when_all_mapped_inputs_still_exist():
    expected = {
        "device": {"kind": "signal", "path": "fields.zabbix.host_name"},
        "tunnel_interface": {"kind": "signal", "path": "fields.tags.interface"},
    }
    reactor = FakeReactor(expected)
    package = FakePackage([reactor])

    changed = prune_stale_reactor_mappings(
        package,
        {"device", "tunnel_interface"},
    )

    assert changed == 0
    assert reactor.get_mappings() == expected
