"""Dynamic inventory choice filtering and extraction regression tests."""

from app.services.project_package_launch import _dynamic_choices_by_host


SPEC = {
    "host_input": "var_hostname",
    "path": "zabbix.selected_items",
    "value_key": "key",
    "key_pattern": r"^oracle\.tablespace\.(?:size|usage)\[([^,]+),(?:size|usage)\]$",
    "extract_group": 1,
    "distinct": True,
    "sort": True,
}


class FakeInput:
    def __init__(self, validation):
        self.validation = validation

    def get_validation(self):
        return self.validation


def test_zabbix_tablespace_choices_extract_unique_sorted_names():
    rows = [
        {"key": "oracle.tablespace.size[USERS,size]", "name": "Tablespace USERS size"},
        {"key": "oracle.tablespace.usage[SYSAUX,usage]", "name": "Tablespace SYSAUX usage %"},
        {"key": "oracle.tablespace.size[SYSAUX,size]", "name": "Tablespace SYSAUX size"},
        {"key": "other.metric[ignored]", "name": "Other"},
    ]
    result = _dynamic_choices_by_host(
        FakeInput({"choices_from_hostvar": SPEC}),
        {"host.example": {"zabbix": {"selected_items": rows}}},
    )["host.example"]
    assert [row["value"] for row in result] == ["SYSAUX", "USERS"]
    assert [row["label"] for row in result] == ["SYSAUX", "USERS"]


def test_legacy_dynamic_choices_keep_labels_and_order():
    spec = {"host_input": "host", "path": "items", "value_key": "key", "label_keys": ["name"]}
    result = _dynamic_choices_by_host(
        FakeInput({"choices_from_hostvar": spec}),
        {"host": {"items": [{"key": "b", "name": "Beta"}, {"key": "a", "name": "Alpha"}]}},
    )["host"]
    assert [(x["value"], x["label"]) for x in result] == [("b", "Beta"), ("a", "Alpha")]


def test_invalid_extraction_validation_is_rejected():
    from app.models.project_package import PACKAGE_INPUT_CHOICE
    
    for changes in (
        {"key_pattern": "["},
        {"extract_group": 2},
        {"extract_group": "1"},
        {"sort": "yes"},
        {"distinct": 1},
    ):
        spec = dict(SPEC, **changes)
        errors = _validate_validation_rules(
            3, PACKAGE_INPUT_CHOICE, {"choices_from_hostvar": spec}
        )
        assert errors, changes
