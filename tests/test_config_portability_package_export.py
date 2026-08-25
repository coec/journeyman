import json

import pytest

from app import db
from app.models import (
    Credential,
    Inventory,
    Project,
    ProjectPackage,
    ProjectSchedule,
    ProjectStep,
    Repository,
)
from app.services.config_portability import (
    ConfigPortabilityError,
    export_configuration,
)


def test_package_export_includes_only_selected_dependency_closure(app):
    with app.app_context():
        credential = Credential(
            name="Network credential",
            owner="admin",
            credential_type="machine",
        )
        db.session.add(credential)
        db.session.flush()

        repository = Repository(
            name="Network repository",
            description="",
            url="git@example.test:network.git",
            default_branch="main",
            credential_id=credential.id,
        )
        source = Inventory(
            name="All network devices",
            inventory_type="satellite",
            endpoint="https://satellite.example.test",
            credential=credential,
            enabled=False,
            config_json=json.dumps(
                {"organization": "Example"}
            ),
        )
        db.session.add_all(
            [repository, source]
        )
        db.session.flush()

        filtered = Inventory(
            name="Cisco switches",
            inventory_type="filtered",
            enabled=True,
            config_json=json.dumps(
                {
                    "source_inventory_id": source.id,
                    "include_groups": [],
                    "exclude_groups": [],
                }
            ),
        )
        selected_project = Project(
            name="Cisco port project",
            enabled=True,
            repository=repository,
            inventory=filtered,
            owner="admin",
        )
        selected_project.credentials.append(
            credential
        )
        selected_project.steps.append(
            ProjectStep(
                position=1,
                name="Control port",
                playbook="port.yml",
                enabled=True,
            )
        )
        selected_project.schedules.append(
            ProjectSchedule(
                name="Do not exchange me",
                schedule_type="once",
                timezone_name="UTC",
                start_at=__import__(
                    "datetime"
                ).datetime.now(
                    __import__("datetime").timezone.utc
                ),
                enabled=True,
                created_by="admin",
            )
        )
        selected_package = ProjectPackage(
            name="Cisco Port Control",
            project=selected_project,
            enabled=True,
            owner="admin",
        )

        unrelated_project = Project(
            name="Unrelated project",
            enabled=True,
            owner="admin",
        )
        unrelated_package = ProjectPackage(
            name="Unrelated Package",
            project=unrelated_project,
            enabled=True,
            owner="admin",
        )

        db.session.add_all(
            [
                filtered,
                selected_package,
                unrelated_package,
            ]
        )
        db.session.commit()

        document = export_configuration(
            package_names=["Cisco Port Control"]
        )

        assert [
            row["name"]
            for row in document["packages"]
        ] == ["Cisco Port Control"]
        assert [
            row["name"]
            for row in document["projects"]
        ] == ["Cisco port project"]

        assert {
            row["name"]
            for row in document["inventories"]
        } == {
            "Cisco switches",
            "All network devices",
        }

        assert document["repositories"] == []
        assert document["projects"][0]["repository"] is None
        assert document["projects"][0]["steps"][0]["repository"] is None

        assert (
            document["projects"][0]["schedules"]
            == []
        )

        assert document["credentials_required"] == [
            {
                "ref": "credential_1",
                "type": "machine",
            }
        ]

        metadata = document["journeyman_export"]
        assert metadata["package_exchange"] is True
        assert metadata["selected_packages"] == [
            "Cisco Port Control"
        ]


def test_package_export_deduplicates_shared_dependencies(app):
    with app.app_context():
        project = Project(
            name="Shared project",
            enabled=True,
            owner="admin",
        )
        package_a = ProjectPackage(
            name="Package A",
            project=project,
            enabled=True,
            owner="admin",
        )
        package_b = ProjectPackage(
            name="Package B",
            project=project,
            enabled=True,
            owner="admin",
        )
        db.session.add_all(
            [package_a, package_b]
        )
        db.session.commit()

        document = export_configuration(
            package_names=[
                "Package A",
                "Package B",
                "Package A",
            ]
        )

        assert {
            row["name"]
            for row in document["packages"]
        } == {
            "Package A",
            "Package B",
        }
        assert [
            row["name"]
            for row in document["projects"]
        ] == ["Shared project"]


def test_package_export_rejects_unknown_package(app):
    with app.app_context():
        with pytest.raises(
            ConfigPortabilityError,
            match="Package\\(s\\) not found",
        ):
            export_configuration(
                package_names=["Does not exist"]
            )


def test_package_and_enabled_only_are_mutually_exclusive(app):
    with app.app_context():
        with pytest.raises(
            ConfigPortabilityError,
            match="cannot be used together",
        ):
            export_configuration(
                enabled_only=True,
                package_names=["Anything"],
            )
