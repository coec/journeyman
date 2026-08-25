import json

import pytest

from app import db
from app.models import Inventory, Project, ProjectPackage, ProjectPackageInput
from app.services.inventory_resolver import (
    InventoryResolutionError,
    resolve_inventory,
)
from app.services.project_package_launch import prepare_package_launch


STATIC_CONTENT = """---
all:
  hosts:
    lab-gateway-01:
      clustername: lab01
      role: gateway
    trn-ap-01:
      clustername: lab01
      role: ap
    lab-gateway-02:
      clustername: lab03
      role: gateway
"""


def _filtered_config(source_id, value):
    return json.dumps(
        {
            "source_inventory_id": source_id,
            "include_groups": [
                {
                    "match": "all",
                    "rules": [
                        {
                            "field": "variable",
                            "parameter": "role",
                            "operator": "equals",
                            "value": "gateway",
                        },
                        {
                            "field": "variable",
                            "parameter": "clustername",
                            "operator": "equals",
                            "value": value,
                        },
                    ],
                }
            ],
            "exclude_groups": [],
        }
    )


def test_filtered_inventory_substitutes_package_binding(app):
    with app.app_context():
        source = Inventory(
            name="All cluster nodes",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps({"content": STATIC_CONTENT}),
        )
        db.session.add(source)
        db.session.flush()

        filtered = Inventory(
            name="Wireless gateways",
            inventory_type="filtered",
            enabled=True,
            config_json=_filtered_config(
                source.id,
                "{{ clustername }}",
            ),
        )
        db.session.add(filtered)
        db.session.commit()

        resolved = resolve_inventory(
            filtered,
            bindings={"clustername": "lab01"},
        )

        assert set(resolved["_meta"]["hostvars"]) == {"lab-gateway-01"}



def test_filtered_inventory_substitutes_multiple_bindings_in_parameter_name(app):
    static_content = """---
all:
  hosts:
    dev02-poa-01:
      foreman_params:
        flag_v687_provisioning_dev02_part1_complete: true
    dev03-poa-01:
      foreman_params:
        flag_v687_provisioning_dev03_part1_complete: true
"""

    with app.app_context():
        source = Inventory(
            name="POA provisioning source",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps({"content": static_content}),
        )
        db.session.add(source)
        db.session.flush()

        filtered = Inventory(
            name="POA cluster provisioning complete",
            inventory_type="filtered",
            enabled=True,
            config_json=json.dumps(
                {
                    "source_inventory_id": source.id,
                    "include_groups": [
                        {
                            "match": "all",
                            "rules": [
                                {
                                    "field": "foreman_param",
                                    "parameter": (
                                        "flag_v{{ target_poa_version }}_"
                                        "provisioning_{{ clustername }}_"
                                        "part1_complete"
                                    ),
                                    "operator": "exists",
                                    "value": "",
                                }
                            ],
                        }
                    ],
                    "exclude_groups": [],
                }
            ),
        )
        db.session.add(filtered)
        db.session.commit()

        resolved = resolve_inventory(
            filtered,
            bindings={
                "target_poa_version": "687",
                "clustername": "dev02",
            },
        )

        assert set(resolved["_meta"]["hostvars"]) == {
            "dev02-poa-01"
        }

def test_filtered_inventory_missing_binding_fails_explicitly(app):
    with app.app_context():
        source = Inventory(
            name="All cluster nodes missing binding",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps({"content": STATIC_CONTENT}),
        )
        db.session.add(source)
        db.session.flush()

        filtered = Inventory(
            name="Wireless gateways missing binding",
            inventory_type="filtered",
            enabled=True,
            config_json=_filtered_config(
                source.id,
                "{{ clustername }}",
            ),
        )
        db.session.add(filtered)
        db.session.commit()

        with pytest.raises(
            InventoryResolutionError,
            match='requires inventory binding "clustername"',
        ):
            resolve_inventory(filtered)


def test_package_choice_can_publish_inventory_binding_under_other_name(app):
    with app.app_context():
        project = Project(
            name="Binding project",
            enabled=True,
            owner="admin",
        )
        package = ProjectPackage(
            name="Binding package",
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
        package_input.set_choices(
            [
                {"value": "lab01", "label": "LAB01"},
                {"value": "lab03", "label": "LAB03"},
            ]
        )
        package.inputs.append(package_input)
        db.session.add(package)
        db.session.commit()

        errors, _fields, prepared = prepare_package_launch(
            package=package,
            form={
                "package_value_{}".format(package_input.id): json.dumps("lab01")
            },
        )

        assert errors == []
        assert prepared.execution_data.execution_vars["cluster"] == "lab01"
        assert prepared.execution_data.inventory_bindings == {
            "clustername": "lab01"
        }


def test_required_package_extra_var_does_not_block_direct_project_dispatch(
    app, monkeypatch
):
    import app.views.projects as projects_view

    # This regression test is specifically about Package prompts.  Keep the
    # Project readiness checks out of scope so an intentionally minimal test
    # Project does not fail first for unrelated configuration reasons.
    monkeypatch.setattr(
        projects_view,
        "_project_dispatch_readiness_issues",
        lambda project: [],
    )

    with app.app_context():
        project = Project(
            name="Direct dispatch project",
            enabled=True,
            owner="admin",
            execution_type="shell",
        )
        package = ProjectPackage(
            name="Prompting package",
            project=project,
            enabled=True,
            owner="admin",
        )
        package.inputs.append(
            ProjectPackageInput(
                position=1,
                variable_name="requested_action",
                label="Requested action",
                input_type="text",
                required=True,
                binding_type="extra_var",
                bind_to_inventory=False,
            )
        )

        assert projects_view._direct_dispatch_block_reason(project) == ""


def test_required_package_inventory_binding_blocks_direct_project_dispatch(app):
    from app.views.projects import _direct_dispatch_block_reason

    with app.app_context():
        project = Project(
            name="Bound dispatch project",
            enabled=True,
            owner="admin",
            execution_type="shell",
        )
        package = ProjectPackage(
            name="Binding package",
            project=project,
            enabled=True,
            owner="admin",
        )
        package.inputs.append(
            ProjectPackageInput(
                position=1,
                variable_name="cluster",
                label="Cluster",
                input_type="text",
                required=True,
                binding_type="extra_var",
                bind_to_inventory=True,
                inventory_binding_name="clustername",
            )
        )

        assert _direct_dispatch_block_reason(project) == (
            "This Project requires Package inventory inputs and can only "
            "be dispatched through a Package."
        )
