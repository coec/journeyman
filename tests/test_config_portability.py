from pathlib import Path
import json

import pytest

from app import db
from app.models import (
    Credential,
    Inventory,
    Project,
    ProjectPackage,
    ProjectPackageInput,
    ProjectStep,
    Repository,
)
from app.services.config_portability import (
    ConfigPortabilityError,
    collect_export_payload,
    export_configuration,
    import_configuration,
    prepare_payload_import,
)


def test_export_never_contains_credential_secret_material(app):
    with app.app_context():
        credential = Credential(
            name="Portable credential",
            owner="admin",
            credential_type="machine",
            username="automation",
            encrypted_data=b"DO_NOT_EXPORT_THIS_SECRET",
            credential_key_id="secret-key-id",
        )
        db.session.add(credential)
        db.session.flush()

        repository = Repository(
            name="Portable repository",
            description="",
            url="git@example.test:repo.git",
            default_branch="main",
            credential_id=credential.id,
        )
        db.session.add(repository)
        db.session.commit()

        document = export_configuration()
        text = json.dumps(document)

        assert "DO_NOT_EXPORT_THIS_SECRET" not in text
        assert "secret-key-id" not in text
        assert "automation" not in text
        assert document["credentials_required"] == []
        assert document["repositories"] == []
        assert "Portable repository" not in text
        assert "git@example.test:repo.git" not in text
        assert "admin" not in json.dumps(
            document["credentials_required"]
        )
        assert (
            document["journeyman_export"]["contains_secret_material"]
            is False
        )
        assert document["journeyman_export"]["internal"] is False


def test_internal_export_includes_repository_and_credential_names(app):
    with app.app_context():
        credential = Credential(
            name="Internal credential",
            owner="admin",
            credential_type="machine",
        )
        db.session.add(credential)
        db.session.flush()
        repository = Repository(
            name="Internal repository",
            description="Private repo",
            url="ssh://git.example.test/automation.git",
            default_branch="main",
            credential_id=credential.id,
        )
        project = Project(
            name="Internal project",
            repository=repository,
            enabled=True,
            owner="admin",
        )
        project.steps.append(
            ProjectStep(
                position=1,
                name="Run",
                repository=repository,
                playbook="site.yml",
                enabled=True,
            )
        )
        db.session.add(project)
        db.session.commit()

        document = export_configuration(internal=True)

        assert document["journeyman_export"]["internal"] is True
        assert document["credentials_required"] == [
            {
                "ref": "credential_1",
                "type": "machine",
                "name": "Internal credential",
            }
        ]
        assert document["repositories"] == [
            {
                "name": "Internal repository",
                "description": "Private repo",
                "repository_type": "git",
                "url": "ssh://git.example.test/automation.git",
                "directory_path": "",
                "default_branch": "main",
                "credential": "credential_1",
            }
        ]
        exported_project = next(
            row for row in document["projects"]
            if row["name"] == "Internal project"
        )
        assert exported_project["repository"] == "Internal repository"
        assert exported_project["steps"][0]["repository"] == "Internal repository"


def test_secret_package_input_default_is_not_exported(app):
    with app.app_context():
        project = Project(
            name="Portable Package Project",
            enabled=True,
            owner="admin",
        )
        package = ProjectPackage(
            name="Portable Package",
            project=project,
            enabled=True,
            owner="admin",
        )
        secret_input = ProjectPackageInput(
            position=1,
            variable_name="password_value",
            label="Password",
            input_type="password",
            is_secret=True,
        )
        secret_input.set_default_value("must-not-export")
        package.inputs.append(secret_input)
        db.session.add(package)
        db.session.commit()

        document = export_configuration()
        exported_package = next(
            item for item in document["packages"]
            if item["name"] == "Portable Package"
        )
        assert exported_package["inputs"][0]["default_value"] is None
        assert "must-not-export" not in json.dumps(document)


def test_filtered_inventory_references_export_by_name(app):
    with app.app_context():
        source = Inventory(
            name="Satellite source",
            inventory_type="satellite",
            endpoint="https://satellite.example.test",
            enabled=True,
            config_json=json.dumps({"organization": "Example"}),
        )
        db.session.add(source)
        db.session.flush()
        filtered = Inventory(
            name="Filtered source",
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
                                    "field": "name",
                                    "operator": "contains",
                                    "value": "app",
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

        document = export_configuration()
        row = next(
            item for item in document["inventories"]
            if item["name"] == "Filtered source"
        )
        assert row["config"]["source_inventory"] == "Satellite source"
        assert "source_inventory_id" not in json.dumps(row)


def test_import_preflight_blocks_missing_credentials_before_changes(app):
    document = {
        "journeyman_export": {
            "format_version": 1,
            "contains_secret_material": False,
        },
        "credentials_required": [
            {
                "ref": "credential_1",
                "name": "Missing",
                "type": "machine",
            }
        ],
        "repositories": [
            {
                "name": "Should Not Import",
                "description": "",
                "url": "git@example.test:repo.git",
                "default_branch": "main",
                "credential": "credential_1",
            }
        ],
    }

    with app.app_context():
        with pytest.raises(
            ConfigPortabilityError,
            match="Missing credential",
        ):
            import_configuration(document)

        assert (
            Repository.query.filter_by(name="Should Not Import").first()
            is None
        )


def test_export_enabled_only_keeps_inventory_dependency_chain(app):
    with app.app_context():
        source = Inventory(
            name="Enabled dependency source",
            inventory_type="satellite",
            endpoint="https://satellite.example.test",
            enabled=False,
            config_json=json.dumps({"organization": "Example"}),
        )
        db.session.add(source)
        db.session.flush()
        filtered = Inventory(
            name="Enabled filtered inventory",
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
        project = Project(
            name="Enabled export project",
            enabled=True,
            inventory=filtered,
            owner="admin",
        )
        project.steps.append(
            ProjectStep(
                position=1,
                name="Run",
                playbook="site.yml",
                enabled=True,
            )
        )
        disabled_project = Project(
            name="Disabled export project",
            enabled=False,
            owner="admin",
        )
        db.session.add_all(
            [filtered, project, disabled_project]
        )
        db.session.commit()

        document = export_configuration(enabled_only=True)

        project_names = {row["name"] for row in document["projects"]}
        inventory_names = {row["name"] for row in document["inventories"]}

        assert "Enabled export project" in project_names
        assert "Disabled export project" not in project_names
        assert "Enabled filtered inventory" in inventory_names
        # Dependency closure wins over source.enabled=False.
        assert "Enabled dependency source" in inventory_names


def test_collect_export_payload_packages_primary_step_file_without_repo_identity(app):
    with app.app_context():
        repository = Repository(
            name="Sensitive repository",
            description="",
            url="ssh://git.example.test/network.git",
            default_branch="main",
        )
        project = Project(
            name="Cisco Port Control",
            repository=repository,
            enabled=True,
            owner="admin",
        )
        project.steps.append(
            ProjectStep(
                position=1,
                name="Control port",
                playbook="private/path/cisco_port_control.yml",
                enabled=True,
            )
        )
        package = ProjectPackage(
            name="Cisco Port Control Package",
            project=project,
            enabled=True,
            owner="admin",
        )
        db.session.add(package)
        db.session.commit()

        checkout = Path(app.config["REPOSITORY_ROOT"]) / str(repository.id)
        source = checkout / "private/path/cisco_port_control.yml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("---\n- hosts: all\n", encoding="utf-8")

        document = export_configuration(
            package_names=["Cisco Port Control Package"]
        )
        files = collect_export_payload(document)

        exported = next(
            item for item in document["projects"]
            if item["name"] == "Cisco Port Control"
        )
        step = exported["steps"][0]
        assert step["payload"] == "asset_1"
        assert step["repository"] is None
        assert step["playbook"] == (
            "journeyman-imports/cisco-port-control/asset_1.yml"
        )
        assert document["repositories"] == []
        assert "Sensitive repository" not in json.dumps(document)
        assert "git.example.test" not in json.dumps(document)
        assert "private/path" not in json.dumps(document)
        assert files["payload/playbooks/asset_1.yml"] == b"---\n- hosts: all\n"
        assert document["payload"][0]["type"] == "ansible_playbook"


def test_prepare_payload_import_rebinds_to_local_repository(app):
    with app.app_context():
        repository = Repository(
            name="Community Automation",
            description="",
            url="git@example.test:community.git",
            default_branch="main",
        )
        db.session.add(repository)
        db.session.commit()

        content = b"#!/bin/bash\necho hello\n"
        import hashlib
        document = {
            "journeyman_export": {
                "format_version": 1,
                "contains_secret_material": False,
                "internal": False,
            },
            "credentials_required": [],
            "repositories": [],
            "payload": [
                {
                    "ref": "asset_1",
                    "type": "shell_script",
                    "path": "payload/scripts/asset_1.sh",
                    "suggested_path": "journeyman-imports/example/asset_1.sh",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            ],
            "projects": [
                {
                    "name": "Example",
                    "repository": None,
                    "steps": [
                        {
                            "position": 1,
                            "repository": None,
                            "playbook": "journeyman-imports/example/asset_1.sh",
                            "payload": "asset_1",
                        }
                    ],
                }
            ],
        }

        writes = prepare_payload_import(
            document,
            {"payload/scripts/asset_1.sh": content},
            "Community Automation",
        )

        assert writes == [
            ("journeyman-imports/example/asset_1.sh", content)
        ]
        assert document["projects"][0]["repository"] == "Community Automation"
        assert document["projects"][0]["steps"][0]["repository"] == "Community Automation"
        assert document["projects"][0]["steps"][0]["playbook"] == (
            "journeyman-imports/example/asset_1.sh"
        )
        assert "payload" not in document["projects"][0]["steps"][0]
