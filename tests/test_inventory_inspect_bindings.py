import json

from app import db
from app.models import Inventory, Project, ProjectPackage, ProjectPackageInput
from app.services.inventory_inspect_bindings import (
    inventory_binding_names,
    packages_for_inventory_bindings,
)
from app.services.project_package_launch import (
    package_inventory_binding_fields,
    prepare_inventory_binding_values,
)


STATIC_CONTENT = """---
all:
  hosts:
    lab-gateway-01:
      clustername: lab01
      role: gateway
    lab-gateway-02:
      clustername: lab03
      role: gateway
"""


def _create_bound_inventory_and_package():
    source = Inventory(
        name="Inspect source",
        inventory_type="static",
        enabled=True,
        config_json=json.dumps({"content": STATIC_CONTENT}),
    )
    db.session.add(source)
    db.session.flush()

    filtered = Inventory(
        name="Inspect bound inventory",
        inventory_type="filtered",
        enabled=True,
        config_json=json.dumps({
            "source_inventory_id": source.id,
            "include_groups": [{
                "match": "all",
                "rules": [{
                    "field": "variable",
                    "parameter": "clustername",
                    "operator": "equals",
                    "value": "{{ clustername }}",
                }],
            }],
            "exclude_groups": [],
        }),
    )
    project = Project(
        name="Inspect bound project",
        inventory=filtered,
        enabled=True,
        owner="admin",
    )
    package = ProjectPackage(
        name="Inspect bound package",
        project=project,
        enabled=True,
        owner="admin",
    )
    package_input = ProjectPackageInput(
        position=1,
        variable_name="cluster",
        label="Cluster",
        input_type="choice",
        required=True,
        binding_type="extra_var",
        bind_to_inventory=True,
        inventory_binding_name="clustername",
    )
    package_input.set_choices([
        {"value": "lab01", "label": "LAB01"},
        {"value": "lab03", "label": "LAB03"},
    ])
    package.inputs.append(package_input)
    db.session.add_all([filtered, project, package])
    db.session.commit()
    return filtered, package, package_input


def test_inspect_binding_discovery_and_validation(app):
    with app.app_context():
        inventory, package, package_input = _create_bound_inventory_and_package()

        assert inventory_binding_names(inventory) == {"clustername"}
        assert packages_for_inventory_bindings(inventory, {"clustername"}) == [package]

        fields = package_inventory_binding_fields(package, {"clustername"})
        assert len(fields) == 1
        assert [choice["label"] for choice in fields[0]["choices"]] == ["LAB01", "LAB03"]

        errors, _fields, bindings = prepare_inventory_binding_values(
            package=package,
            binding_names={"clustername"},
            form={
                "package_value_{}".format(package_input.id): json.dumps("lab01")
            },
        )
        assert errors == []
        assert bindings == {"clustername": "lab01"}


def test_inspect_prompts_for_package_backed_inventory_binding(client, app):
    with app.app_context():
        inventory, package, package_input = _create_bound_inventory_and_package()
        inventory_id = inventory.id
        package_id = package.id
        input_id = package_input.id

    response = client.get(
        "/inventories/{}/preview".format(inventory_id),
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    assert b"Inventory values required" in response.data
    assert b"LAB01" in response.data
    assert b"LAB03" in response.data

    response = client.post(
        "/inventories/{}/preview".format(inventory_id),
        headers={"X-Test-Username": "admin"},
        data={
            "package_id": str(package_id),
            "package_value_{}".format(input_id): json.dumps("lab01"),
        },
    )
    assert response.status_code == 200
    assert b"lab-gateway-01" in response.data
    assert b"lab-gateway-02" not in response.data
