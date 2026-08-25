"""JXF must carry automation definitions, never imported trust."""

import copy

import pytest

from app import db
from app.models import (
    Credential,
    Project,
    ProjectPackage,
)
from app.services.config_portability import (
    ConfigPortabilityError,
    export_configuration,
    import_configuration,
    preflight_import,
)


pytestmark = pytest.mark.security


def _minimal_document():
    return {
        "journeyman_export": {
            "format_version": 1,
            "contains_secret_material": False,
        },
        "credentials_required": [],
        "repositories": [],
        "environments": [],
        "inventories": [],
        "runner_crews": [],
        "projects": [
            {
                "name": "Imported Project",
                "description": "Portable definition",
                "execution_type": "ansible",
                "max_parallel_steps": 1,
                "runner_routing": "local",
                "runner_site": "",
                "runner": None,
                "default_runner": None,
                "default_runner_crew": None,
                "repository": None,
                "inventory": None,
                "environment": None,
                "credentials": [],
                "steps": [],
                "schedules": [],
            }
        ],
        "packages": [
            {
                "name": "Imported Package",
                "description": "Portable Package",
                "project": "Imported Project",
                "warning_message": "",
                "confirmation_required": True,
                "confirmation_message": "",
                "fixed_vars": {},
                "inputs": [],
            }
        ],
    }


@pytest.mark.parametrize(
    "path, mutate, expected",
    [
        (
            "project enabled",
            lambda document: document["projects"][0].update(
                {"enabled": True}
            ),
            "$.projects[0].enabled",
        ),
        (
            "package enabled",
            lambda document: document["packages"][0].update(
                {"enabled": True}
            ),
            "$.packages[0].enabled",
        ),
        (
            "package permissions",
            lambda document: document["packages"][0].update(
                {
                    "permissions": [
                        {
                            "principal_type": "user",
                            "principal_name": "victim",
                        }
                    ]
                }
            ),
            "$.packages[0].permissions",
        ),
        (
            "package access",
            lambda document: document["packages"][0].update(
                {"access_mode": "authenticated"}
            ),
            "$.packages[0].access_mode",
        ),
        (
            "project owner",
            lambda document: document["projects"][0].update(
                {"owner": "someone"}
            ),
            "$.projects[0].owner",
        ),
        (
            "schedule enabled",
            lambda document: document["projects"][0]["schedules"].append(
                {
                    "name": "malicious",
                    "enabled": True,
                }
            ),
            "$.projects[0].schedules[0].enabled",
        ),
    ],
)
def test_import_rejects_jxf_trust_fields(
    app,
    path,
    mutate,
    expected,
):
    document = _minimal_document()
    mutate(document)

    with app.app_context():
        result = preflight_import(document)

    assert any(
        expected in error
        for error in result["errors"]
    ), path


def test_import_assigns_local_restricted_disabled_state(app):
    document = _minimal_document()

    with app.app_context():
        result = import_configuration(document)
        assert result["dry_run"] is False

        project = Project.query.filter_by(
            name="Imported Project"
        ).one()
        package = ProjectPackage.query.filter_by(
            name="Imported Package"
        ).one()

        assert project.enabled is False
        assert project.owner == "system"
        assert project.security_scope == "private"

        assert package.enabled is False
        assert package.owner == "system"
        assert package.access_mode == "restricted"
        assert package.permissions == []


def test_export_does_not_leak_identity_or_permissions(app):
    with app.app_context():
        project = Project(
            name="Identity leak project",
            enabled=True,
            owner="alice",
            security_scope="public",
        )
        package = ProjectPackage(
            name="Identity leak Package",
            project=project,
            enabled=True,
            owner="bob",
            access_mode="authenticated",
        )
        db.session.add(package)
        db.session.commit()

        document = export_configuration(
            package_names=[
                "Identity leak Package"
            ]
        )

    project_row = document["projects"][0]
    package_row = document["packages"][0]

    for forbidden in (
        "enabled",
        "owner",
        "security_scope",
        "builtin_key",
    ):
        assert forbidden not in project_row

    for forbidden in (
        "enabled",
        "owner",
        "access_mode",
        "permissions",
        "builtin_key",
    ):
        assert forbidden not in package_row


def test_credential_requirements_do_not_export_owner(app):
    with app.app_context():
        credential = Credential(
            name="Shared logical credential",
            owner="secret-owner-name",
            credential_type="machine",
        )
        project = Project(
            name="Credential-ref project",
            enabled=True,
            owner="admin",
        )
        project.credentials.append(credential)
        package = ProjectPackage(
            name="Credential-ref Package",
            project=project,
            enabled=True,
            owner="admin",
        )
        db.session.add(package)
        db.session.commit()

        document = export_configuration(
            package_names=[
                "Credential-ref Package"
            ]
        )

    requirement = document[
        "credentials_required"
    ][0]

    assert requirement == {
        "ref": "credential_1",
        "type": "machine",
    }
    assert "Shared logical credential" not in str(document)
    assert "owner" not in requirement
    assert "secret-owner-name" not in str(
        document
    )


def test_import_rejects_owner_inserted_into_credential_requirement(app):
    document = _minimal_document()
    document["credentials_required"] = [
        {
            "ref": "credential_1",
            "name": "Network credential",
            "type": "machine",
            "owner": "leaked-user",
        }
    ]

    with app.app_context():
        result = preflight_import(document)

    assert any(
        "$.credentials_required[0].owner"
        in error
        or "forbidden/unknown" in error
        for error in result["errors"]
    )


def test_import_refuses_existing_inventory_collision_by_default(app):
    """Portable JXF must not silently rewrite an existing live Inventory."""
    from app.models import Inventory
    from tests.checks import assert_output_contains

    document = _minimal_document()
    document["projects"] = []
    document["packages"] = []
    document["inventories"] = [
        {
            "name": "Production Inventory",
            "inventory_type": "zabbix",
            "endpoint": "https://attacker.example.test",
            "credential": None,
            "verify_tls": False,
            "enabled": False,
            "config": {
                "tag_name": "journeyman",
                "tag_value": "imported",
                "include_disabled": True,
            },
        }
    ]

    with app.app_context():
        db.session.add(
            Inventory(
                name="Production Inventory",
                inventory_type="satellite",
                endpoint="https://satellite.example.test",
                verify_tls=True,
                enabled=True,
                config_json='{"organization":"Production"}',
            )
        )
        db.session.commit()

        result = preflight_import(document)
        errors = "\n".join(result["errors"])

        assert_output_contains(
            errors,
            "Inventory 'Production Inventory' already exists",
            purpose=(
                "Verify a portable import collision is rejected before it can "
                "replace an existing Inventory definition."
            ),
        )

        existing = Inventory.query.filter_by(
            name="Production Inventory"
        ).one()
        assert existing.inventory_type == "satellite"
        assert existing.endpoint == "https://satellite.example.test"
        assert existing.verify_tls is True
        assert existing.enabled is True


def test_import_refuses_existing_runner_crew_collision_by_default(app):
    """Portable JXF must not silently change membership of a live Runner Crew."""
    from app.models import RunnerCrew
    from tests.checks import assert_output_contains

    document = _minimal_document()
    document["projects"] = []
    document["packages"] = []
    document["runner_crews"] = [
        {
            "name": "Melbourne Runners",
            "description": "Imported replacement",
            "enabled": False,
            "runners": [],
        }
    ]

    with app.app_context():
        db.session.add(
            RunnerCrew(
                name="Melbourne Runners",
                description="Live crew",
                enabled=True,
            )
        )
        db.session.commit()

        result = preflight_import(document)
        errors = "\n".join(result["errors"])

        assert_output_contains(
            errors,
            "Runner crew 'Melbourne Runners' already exists",
            purpose=(
                "Verify a portable import cannot silently replace an existing "
                "Runner Crew or its membership."
            ),
        )


def test_explicit_replace_existing_allows_intentional_collision(app):
    """Replacement remains available only when the administrator opts in."""
    from app.models import Inventory
    from tests.checks import assert_output_equal

    document = _minimal_document()
    document["projects"] = []
    document["packages"] = []
    document["inventories"] = [
        {
            "name": "Replace Me",
            "inventory_type": "zabbix",
            "endpoint": "https://zabbix.example.test",
            "credential": None,
            "verify_tls": True,
            "enabled": False,
            "config": {
                "tag_name": "journeyman",
                "tag_value": "managed",
                "include_disabled": False,
            },
        }
    ]

    with app.app_context():
        db.session.add(
            Inventory(
                name="Replace Me",
                inventory_type="satellite",
                endpoint="https://satellite.example.test",
                verify_tls=True,
                enabled=True,
                config_json='{"organization":"Production"}',
            )
        )
        db.session.commit()

        result = import_configuration(
            document,
            replace_existing=True,
        )
        imported = Inventory.query.filter_by(name="Replace Me").one()

        assert_output_equal(
            result["counts"]["inventories"]["update"],
            1,
            purpose=(
                "Verify explicit replacement records an update rather than a "
                "new object creation."
            ),
        )
        assert_output_equal(
            imported.inventory_type,
            "zabbix",
            purpose=(
                "Verify --replace-existing deliberately applies the imported "
                "Inventory definition."
            ),
        )
