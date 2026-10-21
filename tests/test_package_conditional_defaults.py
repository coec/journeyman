"""Defaults for conditionally displayed Package inputs."""

import pytest
from werkzeug.datastructures import MultiDict

from app.models import Project, ProjectPackage, ProjectPackageInput
from app.services.project_package_launch import (
    package_launch_fields,
    package_inventory_binding_fields,
    prepare_package_launch,
)


def _package(default):
    project = Project(id=9901, name="Conditional project", owner="admin", enabled=True)
    package = ProjectPackage(id=9902, name="Conditional package", owner="admin", enabled=True, project=project)
    action = ProjectPackageInput(id=9903, position=1, variable_name="pb_action", label="Action", input_type="choice", required=True, binding_type="extra_var")
    action.set_choices([{"label": "Create", "value": "create"}, {"label": "Edit", "value": "edit"}])
    action.set_validation({})
    action.set_conditions({})
    dependent = ProjectPackageInput(id=9904, position=2, variable_name="allow_login_shell", label="Allow login shell", input_type="choice", required=False, binding_type="extra_var", bind_to_inventory=True, inventory_binding_name="login_shell")
    dependent.set_choices([{"label": "True", "value": True}, {"label": "False", "value": False}])
    dependent.set_default_value(default)
    dependent.set_validation({})
    dependent.set_conditions({"visible_when": {"pb_action": "create"}})
    package.inputs.extend([action, dependent])
    return package


@pytest.mark.parametrize("default, key", [(False, "false"), (True, "true")])
def test_hidden_choice_renders_default_without_becoming_visible(default, key):
    package = _package(default)
    fields = package_launch_fields(package)
    assert fields[0]["visible"] is True
    assert fields[1]["visible"] is False
    assert fields[1]["selected_choice_key"] == key
    # Hidden defaults must not be submitted as execution variables.
    errors, _fields, prepared = prepare_package_launch(
        package=package,
        form=MultiDict({"package_value_9903": '"edit"'}),
    )
    assert errors == []
    assert "allow_login_shell" not in prepared.execution_data.execution_vars


def test_conditional_default_is_used_after_field_is_revealed():
    package = _package(False)
    package.inputs[0].set_default_value("create")
    fields = package_launch_fields(package)
    assert fields[1]["visible"] is True
    assert fields[1]["selected_choice_key"] == "false"


def test_inventory_inspect_renders_hidden_choice_default():
    package = _package(False)
    fields = package_inventory_binding_fields(package, {"login_shell"})
    assert len(fields) == 1
    assert fields[0]["visible"] is False
    assert fields[0]["selected_choice_key"] == "false"
