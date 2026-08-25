import json
import hashlib
import pytest

from app import db
from app.models import ApiToken, Job


def _token(secret="jym1_test-secret", username="api-user", role="User"):
    row = ApiToken(
        name="pytest-token-{}".format(username),
        username=username,
        role=role,
        token_digest=hashlib.sha256(secret.encode()).hexdigest(),
        enabled=True,
    )
    db.session.add(row)
    db.session.commit()
    return secret


def test_api_requires_bearer_token(client):
    response = client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"


def test_api_job_info_is_owner_scoped(app, client):
    with app.app_context():
        secret = _token()
        job = Job(project_id=1, project_name="API test", requested_by="api-user", execution_type="shell")
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    response = client.get("/api/v1/jobs/{}".format(job_id), headers={"Authorization": "Bearer " + secret})
    assert response.status_code == 200
    assert response.get_json()["job"]["id"] == job_id


def test_dispatch_module_dispatches_project():
    from ansible_collections.journeyman.operation.plugins.modules.dispatch import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def project_by_name(self, name):
            self.calls.append(("lookup", name))
            return {"id": 42, "name": name}
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload))
            return {"job": {"id": 99, "status": "queued"}}

    client = FakeClient()
    result = execute({"type": "project", "name": "Provision VM"}, client)
    assert result == {"changed": True, "job": {"id": 99, "status": "queued"}}
    assert client.calls == [
        ("lookup", "Provision VM"),
        ("POST", "/api/v1/projects/42/dispatch", {}),
    ]


def test_dispatch_module_dispatches_package_with_inputs():
    from ansible_collections.journeyman.operation.plugins.modules.dispatch import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def package_by_name(self, name):
            self.calls.append(("lookup", name))
            return {"id": 12, "name": name}
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload))
            return {"job": {"id": 101, "status": "queued"}}

    client = FakeClient()
    result = execute({
        "type": "package",
        "name": "Cisco Port Control",
        "inputs": {"interface": "GigabitEthernet1/0/10", "state": "up"},
    }, client)
    assert result == {"changed": True, "job": {"id": 101, "status": "queued"}}
    assert client.calls == [
        ("lookup", "Cisco Port Control"),
        ("POST", "/api/v1/packages/12/dispatch", {
            "inputs": {"interface": "GigabitEthernet1/0/10", "state": "up"},
        }),
    ]


def test_dispatch_module_reruns_job():
    from ansible_collections.journeyman.operation.plugins.modules.dispatch import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload))
            return {
                "source_job_id": 77,
                "job": {"id": 102, "status": "queued"},
            }

    client = FakeClient()
    result = execute({"type": "job", "job_id": 77}, client)
    assert result == {
        "changed": True,
        "source_job_id": 77,
        "job": {"id": 102, "status": "queued"},
    }
    assert client.calls == [("POST", "/api/v1/jobs/77/rerun", {"scope": "all"})]


def test_dispatch_module_reruns_failed_hosts_only():
    from ansible_collections.journeyman.operation.plugins.modules.dispatch import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload))
            return {
                "source_job_id": 77,
                "rerun_scope": "failed",
                "job": {"id": 103, "status": "queued"},
            }

    client = FakeClient()
    result = execute(
        {"type": "job", "job_id": 77, "rerun_scope": "failed"},
        client,
    )
    assert result["job"]["id"] == 103
    assert client.calls == [
        ("POST", "/api/v1/jobs/77/rerun", {"scope": "failed"})
    ]


def test_dispatch_module_rejects_ambiguous_job_arguments():
    from ansible_collections.journeyman.operation.plugins.module_utils.journeyman_api import JourneymanApiError
    from ansible_collections.journeyman.operation.plugins.modules.dispatch import execute

    with pytest.raises(JourneymanApiError, match="name is not valid"):
        execute({"type": "job", "job_id": 77, "name": "Current Project"}, object())


def test_job_info_module_is_read_only():
    from ansible_collections.journeyman.operation.plugins.modules.job_info import execute

    class FakeClient:
        def request(self, method, path, payload=None, query=None):
            assert method == "GET"
            assert path == "/api/v1/jobs/77"
            return {"job": {"id": 77, "status": "successful"}}

    assert execute({"job_id": 77}, FakeClient()) == {
        "changed": False,
        "job": {"id": 77, "status": "successful"},
    }


def test_api_rerun_job_is_owner_scoped(app, client, monkeypatch):
    from types import SimpleNamespace
    import app.api as api_module

    with app.app_context():
        owner_secret = _token(secret="jym1_rerun_owner", username="api-rerun-owner")
        other_secret = _token(secret="jym1_rerun_other", username="api-rerun-other")
        source = Job(
            project_id=1,
            project_name="Rerun API test",
            requested_by="api-rerun-owner",
            execution_type="shell",
            status="successful",
        )
        db.session.add(source)
        db.session.commit()
        source_id = source.id

    def fake_rerun(job, *, requested_by, scope):
        assert job.id == source_id
        assert requested_by == "api-rerun-owner"
        assert scope == "all"
        rerun = Job(
            id=999,
            project_id=job.project_id,
            project_name=job.project_name,
            requested_by=requested_by,
            execution_type=job.execution_type,
            status="queued",
        )
        return SimpleNamespace(job=rerun, source_job=job)

    monkeypatch.setattr(api_module, "rerun_job", fake_rerun)

    forbidden = client.post(
        "/api/v1/jobs/{}/rerun".format(source_id),
        headers={"Authorization": "Bearer " + other_secret},
    )
    assert forbidden.status_code == 403

    response = client.post(
        "/api/v1/jobs/{}/rerun".format(source_id),
        headers={"Authorization": "Bearer " + owner_secret},
    )
    assert response.status_code == 202
    document = response.get_json()
    assert document["source_job_id"] == source_id
    assert document["rerun_scope"] == "all"
    assert document["job"]["id"] == 999
    assert document["job"]["status"] == "queued"


def test_api_cancel_job_is_owner_scoped(app, client):
    with app.app_context():
        owner_secret = _token(secret="jym1_owner", username="api-owner")
        other_secret = _token(secret="jym1_other", username="api-other")
        job = Job(
            project_id=1,
            project_name="Cancel API test",
            requested_by="api-owner",
            execution_type="shell",
            status="queued",
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    forbidden = client.post(
        "/api/v1/jobs/{}/cancel".format(job_id),
        headers={"Authorization": "Bearer " + other_secret},
    )
    assert forbidden.status_code == 403

    response = client.post(
        "/api/v1/jobs/{}/cancel".format(job_id),
        headers={"Authorization": "Bearer " + owner_secret},
    )
    assert response.status_code == 200
    document = response.get_json()
    assert document["changed"] is True
    assert document["job"]["status"] == "cancelled"


def test_job_cancel_module_uses_api_and_preserves_idempotency():
    from ansible_collections.journeyman.operation.plugins.modules.job_cancel import execute

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload))
            return {
                "changed": False,
                "message": "Job #77 is already successful.",
                "job": {"id": 77, "status": "successful"},
            }

    client = FakeClient()
    result = execute({"job_id": 77}, client)
    assert result == {
        "changed": False,
        "message": "Job #77 is already successful.",
        "job": {"id": 77, "status": "successful"},
    }
    assert client.calls == [
        ("POST", "/api/v1/jobs/77/cancel", {}),
    ]


def test_api_package_form_rejects_undeclared_inputs(app):
    from app.api import _package_form
    from app.models import ProjectPackage, ProjectPackageInput
    from app.services.project_package_launch import PackageLaunchError

    with app.app_context():
        package = ProjectPackage(name="API package form", project_id=1, owner="admin")
        db.session.add(package)
        db.session.flush()
        package.inputs.append(ProjectPackageInput(
            position=1,
            variable_name="interface",
            label="Interface",
            input_type="text",
            binding_type="extra_var",
        ))
        db.session.flush()
        try:
            _package_form(package, {"not_declared": "bad"})
        except PackageLaunchError as exc:
            assert "Unknown Package input" in str(exc)
        else:
            raise AssertionError("undeclared Package input was accepted")


def test_configuration_repository_module_is_declarative():
    from ansible_collections.journeyman.configuration.plugins.modules.repository import execute

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload, query))
            return {
                "changed": False,
                "message": 'Repository "SysAdmin" is already configured.',
                "repository": {"id": 8, "name": "SysAdmin"},
            }

    client = FakeClient()
    result = execute({
        "name": "SysAdmin",
        "repository_type": "git",
        "url": "ssh://git@gitlab.example/sysadmin/ansible.git",
        "default_branch": "main",
        "credential": "",
        "state": "present",
    }, client)
    assert result["changed"] is False
    assert client.calls == [(
        "PUT",
        "/api/v1/repositories/by-name",
        {
            "name": "SysAdmin",
            "description": "",
            "repository_type": "git",
            "url": "ssh://git@gitlab.example/sysadmin/ansible.git",
            "directory_path": "",
            "default_branch": "main",
            "credential": "",
        },
        None,
    )]


def test_api_repository_configuration_requires_administrator(app, client):
    with app.app_context():
        secret = _token(secret="jym1_repo-user", username="repo-user", role="User")
    response = client.put(
        "/api/v1/repositories/by-name",
        json={"name": "Nope", "url": "ssh://git@example.invalid/repo.git"},
        headers={"Authorization": "Bearer " + secret},
    )
    assert response.status_code == 403


def test_repository_configuration_service_is_idempotent(app, monkeypatch):
    from app.models import Repository
    from app.services import repository_configuration

    with app.app_context():
        monkeypatch.setattr(repository_configuration, "validate_repository_url", lambda value: value)
        values = {
            "name": "API Repository",
            "description": "Configured by API",
            "repository_type": "git",
            "url": "ssh://git@gitlab.example/sysadmin/repo.git",
            "default_branch": "main",
            "credential": "",
        }
        first = repository_configuration.configure_repository(values)
        second = repository_configuration.configure_repository(values)
        assert first.changed is True
        assert second.changed is False
        assert Repository.query.filter_by(name="API Repository").count() == 1


def test_configuration_project_module_submits_whole_workflow():
    from ansible_collections.journeyman.configuration.plugins.modules.project import execute

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload, query))
            return {
                "changed": True,
                "message": 'Project "Provision VM" created.',
                "project": {"name": "Provision VM"},
            }

    params = {
        "name": "Provision VM",
        "description": "Provision and configure a VM",
        "execution_type": "ansible",
        "repository": "SysAdmin",
        "inventory": "VMware",
        "environment": "Ansible 2.18",
        "credentials": ["VMware"],
        "max_parallel_steps": 2,
        "concurrency_policy": "exclusive",
        "oversight_required_between_all_steps": False,
        "enabled": True,
        "steps": [
            {
                "name": "Create VM",
                "playbook": "vmware/create.yml",
            },
            {
                "name": "Configure VM",
                "playbook": "linux/configure.yml",
                "depends_on": ["Create VM"],
            },
        ],
        "state": "present",
    }

    client = FakeClient()
    result = execute(params, client)

    assert result["changed"] is True
    assert client.calls == [(
        "PUT",
        "/api/v1/project-configurations/by-name",
        {
            "name": "Provision VM",
            "description": "Provision and configure a VM",
            "execution_type": "ansible",
            "inventory": "VMware",
            "repository": "SysAdmin",
            "environment": "Ansible 2.18",
            "credentials": ["VMware"],
            "max_parallel_steps": 2,
            "concurrency_policy": "exclusive",
            "oversight_required_between_all_steps": False,
            "enabled": True,
            "steps": params["steps"],
        },
        None,
    )]


def test_project_configuration_service_creates_multistep_project_and_is_idempotent(app):
    from app.models import Project, Repository
    from app.services.project_configuration import configure_project

    with app.app_context():
        repository = Repository(
            name="Project API Repository",
            repository_type="git",
            url="ssh://git@example.invalid/project-api.git",
            default_branch="main",
        )
        db.session.add(repository)
        db.session.commit()

        values = {
            "name": "Project API Workflow",
            "repository": repository.name,
            "concurrency_policy": "exclusive",
            "steps": [
                {
                    "name": "Prepare",
                    "playbook": "prepare.yml",
                    "extra_vars": {"phase": "prepare"},
                },
                {
                    "name": "Apply",
                    "playbook": "apply.yml",
                    "depends_on": ["Prepare"],
                    "extra_vars": {"phase": "apply"},
                },
                {
                    "name": "Verify",
                    "playbook": "verify.yml",
                    "depends_on": ["Apply"],
                },
            ],
        }

        first = configure_project(values, owner="api-admin")
        second = configure_project(values, owner="api-admin")

        assert first.changed is True
        assert second.changed is False

        project = Project.query.filter_by(name="Project API Workflow").one()
        assert project.owner == "api-admin"
        assert project.concurrency_policy == "exclusive"
        assert [step.name for step in project.steps] == ["Prepare", "Apply", "Verify"]
        assert project.steps[1].get_dependency_positions() == [1]
        assert project.steps[2].get_dependency_positions() == [2]
        assert project.steps[0].get_extra_vars() == {"phase": "prepare"}


def test_project_configuration_service_updates_step_and_reports_changed(app):
    from app.models import Project
    from app.services.project_configuration import configure_project

    with app.app_context():
        values = {
            "name": "Project API Update",
            "steps": [{"name": "Only step", "playbook": "before.yml"}],
        }
        assert configure_project(values).changed is True

        values["steps"][0]["playbook"] = "after.yml"
        result = configure_project(values)

        assert result.changed is True
        project = Project.query.filter_by(name="Project API Update").one()
        assert project.steps[0].playbook == "after.yml"


def test_project_configuration_rejects_duplicate_step_names(app):
    from app.services.project_configuration import (
        ProjectConfigurationError,
        configure_project,
    )

    with app.app_context():
        try:
            configure_project({
                "name": "Duplicate step API test",
                "steps": [
                    {"name": "Same", "playbook": "one.yml"},
                    {"name": "Same", "playbook": "two.yml"},
                ],
            })
        except ProjectConfigurationError as exc:
            assert 'Step name "Same" is duplicated.' in str(exc)
        else:
            raise AssertionError("duplicate Project step names were accepted")


def test_project_configuration_allows_incomplete_draft(app):
    from app.models import Project
    from app.services.project_configuration import configure_project

    with app.app_context():
        result = configure_project({
            "name": "Incomplete API Project",
            "enabled": False,
            "steps": [{"name": "Draft step"}],
        })

        assert result.changed is True
        project = Project.query.filter_by(name="Incomplete API Project").one()
        assert project.enabled is False
        assert project.steps[0].playbook == ""


def test_project_configuration_refuses_builtin_project_changes(app):
    from app.models import Project
    from app.services.project_configuration import (
        ProjectConfigurationError,
        configure_project,
    )

    with app.app_context():
        project = Project(
            name="Built-in API Project",
            builtin_key="pytest-api-project",
            owner="system",
        )
        db.session.add(project)
        db.session.commit()

        try:
            configure_project({
                "name": project.name,
                "steps": [{"name": "Step 1", "playbook": "changed.yml"}],
            })
        except ProjectConfigurationError as exc:
            assert "Built-in Projects cannot be modified" in str(exc)
        else:
            raise AssertionError("built-in Project was modified through configuration service")


def test_project_configuration_delete_is_idempotent(app):
    from app.models import Project
    from app.services.project_configuration import configure_project, delete_project

    with app.app_context():
        configure_project({
            "name": "Delete API Project",
            "steps": [{"name": "Step 1", "playbook": "delete.yml"}],
        })

        first = delete_project("Delete API Project")
        second = delete_project("Delete API Project")

        assert first.changed is True
        assert second.changed is False
        assert Project.query.filter_by(name="Delete API Project").count() == 0


def test_configuration_project_module_state_absent_uses_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.project import execute

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload, query))
            return {
                "changed": False,
                "message": 'Project "Gone" is already absent.',
                "project": None,
            }

    client = FakeClient()
    result = execute({"name": "Gone", "state": "absent"}, client)

    assert result["changed"] is False
    assert client.calls == [(
        "DELETE",
        "/api/v1/project-configurations/by-name",
        None,
        {"name": "Gone"},
    )]


def test_configuration_inventory_module_submits_filtered_inventory():
    from ansible_collections.journeyman.configuration.plugins.modules.inventory import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload, query))
            return {"changed": True, "message": "created", "inventory": {"name": "safe"}}

    params = {
        "name": "safe",
        "inventory_type": "filtered",
        "enabled": True,
        "source_inventory": "base",
        "include_groups": [],
        "exclude_groups": [{
            "match": "all",
            "rules": [{"field": "group", "operator": "equals", "value": "manual_patch_exclusions"}],
        }],
        "source_inventories": [],
        "verify_tls": True,
        "tag_value": "journeyman",
        "include_disabled": False,
        "state": "present",
    }
    client = FakeClient()
    result = execute(params, client)
    assert result["changed"] is True
    assert client.calls[0][0:2] == ("PUT", "/api/v1/inventory-configurations/by-name")
    assert client.calls[0][2]["source_inventory"] == "base"
    assert client.calls[0][2]["exclude_groups"][0]["rules"][0]["value"] == "manual_patch_exclusions"


def test_inventory_configuration_static_create_is_idempotent(app):
    from app.models import Inventory
    from app.services.inventory_configuration import configure_inventory

    with app.app_context():
        values = {
            "name": "Static API Inventory",
            "inventory_type": "static",
            "content": "all:\n  hosts:\n    host01: {}\n",
            "enabled": True,
        }
        first = configure_inventory(values)
        second = configure_inventory(values)
        assert first.changed is True
        assert second.changed is False
        row = Inventory.query.filter_by(name="Static API Inventory").one()
        assert row.inventory_type == "static"


def test_inventory_configuration_filtered_uses_source_name_and_updates(app):
    from app.models import Inventory
    from app.services.inventory_configuration import configure_inventory

    with app.app_context():
        source = Inventory(
            name="Base API Inventory",
            inventory_type="static",
            enabled=True,
            config_json='{"content": "all: {}"}',
        )
        db.session.add(source)
        db.session.commit()

        values = {
            "name": "Filtered API Inventory",
            "inventory_type": "filtered",
            "source_inventory": source.name,
            "include_groups": [{
                "match": "all",
                "rules": [{"field": "hostname", "operator": "contains", "value": "rtr"}],
            }],
            "exclude_groups": [],
        }
        assert configure_inventory(values).changed is True
        assert configure_inventory(values).changed is False
        values["include_groups"][0]["rules"][0]["value"] = "srv"
        assert configure_inventory(values).changed is True

        row = Inventory.query.filter_by(name="Filtered API Inventory").one()
        config = json.loads(row.config_json)
        assert config["source_inventory_id"] == source.id
        assert config["include_groups"][0]["rules"][0]["value"] == "srv"


def test_inventory_configuration_composite_requires_two_sources(app):
    from app.models import Inventory
    from app.services.inventory_configuration import InventoryConfigurationError, configure_inventory

    with app.app_context():
        source = Inventory(name="Only API Source", inventory_type="static", enabled=True, config_json='{"content":"all: {}"}')
        db.session.add(source)
        db.session.commit()
        try:
            configure_inventory({
                "name": "Bad Composite API Inventory",
                "inventory_type": "composite",
                "source_inventories": [source.name],
            })
        except InventoryConfigurationError as exc:
            assert "at least two source inventories" in str(exc)
        else:
            raise AssertionError("single-source Composite Inventory was accepted")


def test_inventory_configuration_delete_is_idempotent_and_dependency_safe(app):
    from app.models import Inventory
    from app.services.inventory_configuration import InventoryConfigurationError, configure_inventory, delete_inventory

    with app.app_context():
        configure_inventory({
            "name": "Delete Inventory API Source",
            "inventory_type": "static",
            "content": "all: {}",
        })
        configure_inventory({
            "name": "Delete Inventory API Child",
            "inventory_type": "filtered",
            "source_inventory": "Delete Inventory API Source",
            "include_groups": [],
            "exclude_groups": [],
        })
        try:
            delete_inventory("Delete Inventory API Source")
        except InventoryConfigurationError as exc:
            assert "used by" in str(exc)
        else:
            raise AssertionError("source Inventory was deleted while still referenced")

        assert delete_inventory("Delete Inventory API Child").changed is True
        assert delete_inventory("Delete Inventory API Child").changed is False
        assert delete_inventory("Delete Inventory API Source").changed is True
        assert Inventory.query.filter_by(name="Delete Inventory API Source").count() == 0


def test_configuration_inventory_module_state_absent_uses_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.inventory import execute

    class FakeClient:
        def __init__(self):
            self.calls = []
        def request(self, method, path, payload=None, query=None):
            self.calls.append((method, path, payload, query))
            return {"changed": False, "message": "already absent", "inventory": None}

    client = FakeClient()
    result = execute({"name": "Gone", "state": "absent"}, client)
    assert result["changed"] is False
    assert client.calls == [("DELETE", "/api/v1/inventory-configurations/by-name", None, {"name": "Gone"})]


def test_configuration_package_module_submits_inputs():
    from ansible_collections.journeyman.configuration.plugins.modules.package import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "PUT"
            assert path == "/api/v1/package-configurations/by-name"
            payload = kwargs["payload"]
            assert payload["project"] == "Package API Project"
            assert payload["inputs"][0]["name"] == "interface"
            return {"changed": True, "package": payload}

    result = execute({
        "name": "Package API",
        "project": "Package API Project",
        "inputs": [{"name": "interface", "label": "Interface", "type": "text"}],
    }, Client())
    assert result["changed"] is True


def test_package_configuration_service_create_and_idempotent(app):
    from app.models import Project, ProjectPackage
    from app.services.package_configuration import configure_package

    with app.app_context():
        project = Project(name="Package API Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        values = {
            "name": "Package API",
            "project": project.name,
            "fixed_vars": {"mode": "safe"},
            "inputs": [{
                "name": "interface", "label": "Interface", "type": "text", "required": True,
                "validation": {"pattern": "^Gi"},
            }],
            "permissions": [{"type": "group", "name": "Automation Users"}],
        }
        assert configure_package(values, owner="api-admin").changed is True
        assert configure_package(values, owner="api-admin").changed is False
        package = ProjectPackage.query.filter_by(name="Package API").one()
        assert package.owner == "api-admin"
        assert package.inputs[0].get_validation()["pattern"] == "^Gi"
        assert package.permissions[0].principal_name == "Automation Users"


def test_package_configuration_service_updates_input(app):
    from app.models import Project
    from app.services.package_configuration import configure_package

    with app.app_context():
        project = Project(name="Package Update Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        values = {"name": "Package Update", "project": project.name, "inputs": [{"name": "target", "label": "Target", "type": "text"}]}
        assert configure_package(values).changed is True
        values["inputs"][0]["label"] = "Updated target"
        assert configure_package(values).changed is True
        assert configure_package(values).changed is False


def test_package_configuration_rejects_fixed_input_collision(app):
    from app.models import Project
    from app.services.package_configuration import PackageConfigurationError, configure_package

    with app.app_context():
        project = Project(name="Package Collision Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        with pytest.raises(PackageConfigurationError, match="conflicts with a fixed variable"):
            configure_package({
                "name": "Package Collision", "project": project.name,
                "fixed_vars": {"target": "fixed"},
                "inputs": [{"name": "target", "label": "Target", "type": "text"}],
            })


def test_configuration_package_module_state_absent_uses_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.package import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "DELETE"
            assert path == "/api/v1/package-configurations/by-name"
            assert kwargs["query"] == {"name": "Package API"}
            return {"changed": True, "package": None}

    assert execute({"name": "Package API", "state": "absent"}, Client())["changed"] is True


def test_configuration_schedule_module_submits_weekly_schedule():
    from ansible_collections.journeyman.configuration.plugins.modules.schedule import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "PUT"
            assert path == "/api/v1/schedule-configurations/by-name"
            payload = kwargs["payload"]
            assert payload["project"] == "Schedule API Project"
            assert payload["schedule_type"] == "weekly"
            assert payload["weekdays"] == [0, 2, 4]
            return {"changed": True, "schedule": payload}

    result = execute({
        "name": "Weekdays",
        "project": "Schedule API Project",
        "schedule_type": "weekly",
        "timezone": "UTC",
        "start_at": "2099-01-01T10:00",
        "weekdays": [0, 2, 4],
    }, Client())
    assert result["changed"] is True


def test_schedule_configuration_create_and_idempotent(app):
    from app.models import Project, ProjectSchedule
    from app.services.schedule_configuration import configure_schedule

    with app.app_context():
        project = Project(name="Schedule API Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        values = {
            "name": "Nightly",
            "project": project.name,
            "schedule_type": "daily",
            "timezone": "UTC",
            "start_at": "2099-01-01T02:00",
            "enabled": True,
        }
        first = configure_schedule(values, created_by="api-admin")
        second = configure_schedule(values, created_by="api-admin")
        assert first.changed is True
        assert second.changed is False
        row = ProjectSchedule.query.filter_by(project_id=project.id, name="Nightly").one()
        assert row.created_by == "api-admin"
        assert row.schedule_type == "daily"


def test_schedule_configuration_updates_interval(app):
    from app.models import Project
    from app.services.schedule_configuration import configure_schedule

    with app.app_context():
        project = Project(name="Schedule Update Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        values = {
            "name": "Frequent",
            "project": project.name,
            "schedule_type": "interval",
            "timezone": "UTC",
            "start_at": "2099-01-01T00:00",
            "interval_minutes": 60,
        }
        assert configure_schedule(values).changed is True
        values["interval_minutes"] = 30
        assert configure_schedule(values).changed is True
        assert configure_schedule(values).changed is False


def test_schedule_configuration_weekly_requires_weekday(app):
    from app.models import Project
    from app.services.schedule_configuration import ScheduleConfigurationError, configure_schedule

    with app.app_context():
        project = Project(name="Schedule Validation Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        with pytest.raises(ScheduleConfigurationError, match="Select at least one weekday"):
            configure_schedule({
                "name": "Bad weekly",
                "project": project.name,
                "schedule_type": "weekly",
                "timezone": "UTC",
                "start_at": "2099-01-01T10:00",
                "weekdays": [],
            })


def test_configuration_schedule_module_state_absent_uses_project_and_name():
    from ansible_collections.journeyman.configuration.plugins.modules.schedule import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "DELETE"
            assert path == "/api/v1/schedule-configurations/by-name"
            assert kwargs["query"] == {"project": "My Project", "name": "Nightly"}
            return {"changed": False, "schedule": None}

    result = execute({
        "name": "Nightly",
        "project": "My Project",
        "state": "absent",
    }, Client())
    assert result["changed"] is False


def test_schedule_configuration_delete_is_idempotent(app):
    from app.models import Project
    from app.services.schedule_configuration import configure_schedule, delete_schedule

    with app.app_context():
        project = Project(name="Schedule Delete Project", execution_type="ansible", owner="admin")
        db.session.add(project)
        db.session.commit()
        configure_schedule({
            "name": "Delete me",
            "project": project.name,
            "schedule_type": "once",
            "timezone": "UTC",
            "start_at": "2099-01-01T10:00",
        })
        assert delete_schedule(project.name, "Delete me").changed is True
        assert delete_schedule(project.name, "Delete me").changed is False


def test_credential_configuration_service_secret_idempotency(app):
    from app.models import Credential
    from app.services.credential_configuration import configure_credential

    with app.app_context():
        values = {
            "name": "API Machine Credential",
            "description": "Managed declaratively",
            "credential_type": "machine",
            "security_scope": "private",
            "username": "ansible",
            "credential_data": {
                "password": "super-secret",
                "become_method": "sudo",
                "become_user": "root",
                "extra_vars": {"ansible_connection": "ssh"},
            },
        }
        first = configure_credential(values, owner="api-admin")
        second = configure_credential(values, owner="api-admin")
        assert first.changed is True
        assert second.changed is False
        row = Credential.query.filter_by(owner="api-admin", name="API Machine Credential").one()
        assert row.get_credential_data()["password"] == "super-secret"


def test_credential_configuration_update_omitted_secret_is_retained(app):
    from app.models import Credential
    from app.services.credential_configuration import configure_credential

    with app.app_context():
        configure_credential({
            "name": "Retain Secret",
            "credential_type": "windows",
            "username": "DOMAIN\\svc_ansible",
            "credential_data": {"password": "initial", "extra_vars": {"ansible_port": 5986}},
        }, owner="api-admin")
        result = configure_credential({
            "name": "Retain Secret",
            "description": "Updated",
            "credential_type": "windows",
            "username": "DOMAIN\\svc_ansible",
            "credential_data": {"extra_vars": {"ansible_port": 5986}},
        }, owner="api-admin")
        assert result.changed is True
        assert configure_credential({
            "name": "Retain Secret",
            "description": "Updated",
            "credential_type": "windows",
            "username": "DOMAIN\\svc_ansible",
            "credential_data": {"extra_vars": {"ansible_port": 5986}},
        }, owner="api-admin").changed is False
        row = Credential.query.filter_by(owner="api-admin", name="Retain Secret").one()
        assert row.get_credential_data()["password"] == "initial"


def test_api_credential_configuration_does_not_return_secret_values(app, client):
    with app.app_context():
        secret = _token(secret="jym1_credential-admin", username="credential-admin", role="Administrator")
    response = client.put(
        "/api/v1/credential-configurations/by-name",
        json={
            "name": "API Zabbix",
            "credential_type": "zabbix",
            "credential_data": {"token": "zabbix-secret-token"},
        },
        headers={"Authorization": "Bearer " + secret},
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "zabbix-secret-token" not in text
    document = response.get_json()["credential"]
    assert document["credential_data"] == {}
    assert document["populated_secret_fields"] == ["token"]


def test_configuration_credential_module_submits_no_log_payload_shape():
    from ansible_collections.journeyman.configuration.plugins.modules.credential import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "PUT"
            assert path == "/api/v1/credential-configurations/by-name"
            payload = kwargs["payload"]
            assert payload["credential_type"] == "source_control"
            assert payload["username"] == "gitlab-user"
            assert payload["credential_data"] == {"password": "access-token"}
            return {"changed": True, "credential": {"name": payload["name"]}}

    result = execute({
        "name": "GitLab",
        "credential_type": "source_control",
        "username": "gitlab-user",
        "credential_data": {"password": "access-token"},
    }, Client())
    assert result["changed"] is True


def test_configuration_credential_module_state_absent_uses_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.credential import execute

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "DELETE"
            assert path == "/api/v1/credential-configurations/by-name"
            assert kwargs["query"] == {"name": "Old Credential"}
            return {"changed": False, "credential": None}

    assert execute({"name": "Old Credential", "state": "absent"}, Client())["changed"] is False


def test_signal_source_configuration_create_and_secret_idempotency(app):
    from app.models import SignalSource
    from app.services.signal_source_configuration import configure_signal_source, signal_source_configuration_document

    with app.app_context():
        values = {
            "name": "API Zabbix Source",
            "description": "Managed declaratively",
            "source_type": "zabbix",
            "enabled": True,
            "zabbix_url": "https://zabbix.example/",
            "allowed_networks": ["192.0.2.0/24"],
            "hmac_secret": "source-hmac-secret",
        }
        first = configure_signal_source(values)
        second = configure_signal_source(values)
        assert first.changed is True
        assert second.changed is False
        row = SignalSource.query.filter_by(name="API Zabbix Source").one()
        assert row.get_hmac_secret() == "source-hmac-secret"
        document = signal_source_configuration_document(row)
        assert document["hmac_secret_configured"] is True
        assert "source-hmac-secret" not in str(document)


def test_signal_source_configuration_omitted_secret_is_retained(app):
    from app.models import SignalSource
    from app.services.signal_source_configuration import configure_signal_source

    with app.app_context():
        configure_signal_source({
            "name": "Retain Source Secret",
            "source_type": "zabbix",
            "zabbix_url": "https://zabbix.example/",
            "allowed_networks": ["10.0.0.0/8"],
            "hmac_secret": "initial-secret",
        })
        result = configure_signal_source({
            "name": "Retain Source Secret",
            "description": "Updated",
            "source_type": "zabbix",
            "zabbix_url": "https://zabbix.example/",
            "allowed_networks": ["10.0.0.0/8"],
        })
        assert result.changed is True
        assert configure_signal_source({
            "name": "Retain Source Secret",
            "description": "Updated",
            "source_type": "zabbix",
            "zabbix_url": "https://zabbix.example/",
            "allowed_networks": ["10.0.0.0/8"],
        }).changed is False
        row = SignalSource.query.filter_by(name="Retain Source Secret").one()
        assert row.get_hmac_secret() == "initial-secret"


def test_reactor_configuration_create_and_idempotent(app):
    from app.models import Project, ProjectPackage, SignalSource
    from app.services.reactor_configuration import configure_reactor

    with app.app_context():
        project = Project(name="Reactor API Project", execution_type="ansible", owner="admin")
        package = ProjectPackage(
            name="Reactor API Package",
            project=project,
            owner="admin",
            allow_as_reaction=True,
        )
        source = SignalSource(
            name="Reactor API Source",
            source_type="zabbix",
            zabbix_url="https://zabbix.example/",
        )
        source.set_allowed_networks(["192.0.2.0/24"])
        source.set_hmac_secret("reactor-source-secret")
        db.session.add_all([project, package, source])
        db.session.commit()

        values = {
            "name": "API Reactor",
            "description": "Managed declaratively",
            "source": source.name,
            "package": package.name,
            "mode": "automatic",
            "match": {"all": [{"field": "host", "operator": "contains", "value": "rtr"}]},
            "mappings": {},
            "cooldown_seconds": 60,
            "max_concurrency": 2,
            "enabled": True,
        }
        assert configure_reactor(values).changed is True
        assert configure_reactor(values).changed is False


def test_configuration_signal_source_module_payload_and_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.signal_source import execute

    class Client:
        def request(self, method, path, **kwargs):
            if method == "PUT":
                assert path == "/api/v1/signal-source-configurations/by-name"
                assert kwargs["payload"]["hmac_secret"] == "secret"
                return {"changed": True, "signal_source": {"name": "Zabbix"}}
            assert method == "DELETE"
            assert path == "/api/v1/signal-source-configurations/by-name"
            assert kwargs["query"] == {"name": "Zabbix"}
            return {"changed": True, "signal_source": None}

    assert execute({
        "name": "Zabbix", "source_type": "zabbix", "zabbix_url": "https://zabbix.example/",
        "allowed_networks": ["10.0.0.0/8"], "hmac_secret": "secret", "state": "present",
    }, Client())["changed"] is True
    assert execute({"name": "Zabbix", "state": "absent"}, Client())["changed"] is True


def test_configuration_reactor_module_payload_and_delete():
    from ansible_collections.journeyman.configuration.plugins.modules.reactor import execute

    class Client:
        def request(self, method, path, **kwargs):
            if method == "PUT":
                assert path == "/api/v1/reactor-configurations/by-name"
                assert kwargs["payload"]["source"] == "Zabbix"
                assert kwargs["payload"]["package"] == "Recover GRE"
                return {"changed": True, "reactor": {"name": "GRE recovery"}}
            assert method == "DELETE"
            assert path == "/api/v1/reactor-configurations/by-name"
            assert kwargs["query"] == {"name": "GRE recovery"}
            return {"changed": True, "reactor": None}

    assert execute({
        "name": "GRE recovery", "source": "Zabbix", "package": "Recover GRE",
        "match": {"all": []}, "mappings": {}, "state": "present",
    }, Client())["changed"] is True
    assert execute({"name": "GRE recovery", "state": "absent"}, Client())["changed"] is True
