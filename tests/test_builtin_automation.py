from pathlib import Path

from app import db
from app.models import Credential, Project, ProjectPackage, ProjectSchedule, Repository
from app.services.builtin_automation import (
    BACKUP_BUILTIN_KEY,
    BACKUP_PACKAGE_NAME,
    BACKUP_PROJECT_NAME,
    BACKUP_SCHEDULE_NAME,
    BUILTIN_OWNER,
    BUILTIN_REPOSITORY_NAME,
    REMOTE_RUNNER_BUILTIN_KEY,
    REMOTE_RUNNER_PACKAGE_NAME,
    REMOTE_RUNNER_PROJECT_NAME,
    ensure_builtin_admin_automation,
)


def test_builtin_remote_runner_automation_is_seeded(app):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()

        project = Project.query.filter_by(name=REMOTE_RUNNER_PROJECT_NAME).one()
        package = ProjectPackage.query.filter_by(name=REMOTE_RUNNER_PACKAGE_NAME).one()

        assert project.owner == BUILTIN_OWNER
        assert package.owner == BUILTIN_OWNER
        assert project.builtin_key == REMOTE_RUNNER_BUILTIN_KEY
        assert package.builtin_key == REMOTE_RUNNER_BUILTIN_KEY
        assert package.access_mode == "restricted"
        assert package.permissions == []
        assert project.runner_routing == "local"
        assert project.default_runner_id is None
        assert len(project.steps) == 1
        assert project.steps[0].playbook == "manage-remote-runner.yml"

        input_names = [item.variable_name for item in package.inputs]
        assert input_names == [
            "journeyman_manage_action",
            "journeyman_runner_host",
            "journeyman_runner_name",
            "journeyman_bootstrap_credential_id",
            "journeyman_runner_site",
            "journeyman_runner_max_concurrent_steps",
            "journeyman_pip_proxy_required",
            "journeyman_pip_proxy_credential_id",
            "journeyman_remove_runner_software",
        ]
        assert "journeyman_registration_token" not in input_names
        assert "journeyman_server_url" not in input_names
        assert package.get_fixed_vars()["journeyman_server_url"] == (
            "https://journeyman.example.com"
        )

        remove_input = next(
            item for item in package.inputs
            if item.variable_name == "journeyman_remove_runner_software"
        )
        assert remove_input.get_conditions()["visible_when"][
            "journeyman_manage_action"
        ] == ["unregister", "delete"]

        checkout = Path(app.config["REPOSITORY_ROOT"]) / str(seeded["repository"].id)
        assert (checkout / ".git").is_dir()
        assert (checkout / "manage-remote-runner.yml").is_file()
        assert (checkout / "journeyman-backup-restore.yml").is_file()



def test_builtin_remote_runner_proxy_input_lists_url_credentials(app):
    with app.app_context():
        credential = Credential(
            name="Runner pip proxy",
            owner="admin",
            credential_type="url",
            username="",
        )
        credential.set_credential_data({
            "url": "http://proxy.example.com:8080",
            "auth_mode": "none",
        })
        db.session.add(credential)
        db.session.commit()

        package = ensure_builtin_admin_automation()["package"]
        proxy_input = next(
            item for item in package.inputs
            if item.variable_name == "journeyman_pip_proxy_credential_id"
        )
        assert proxy_input.input_type == "choice"
        assert proxy_input.get_choices() == [
            {"value": credential.id, "label": "Runner pip proxy"}
        ]
        assert proxy_input.is_secret is False


def test_builtin_remote_runner_bootstrap_input_lists_linux_machine_credentials(app):
    with app.app_context():
        linux = Credential(
            name="src_ansibilion",
            owner="admin",
            credential_type="machine",
            username="ansibilion",
        )
        linux.set_credential_data({"password": "secret", "become_password": "sudo-secret"})
        windows = Credential(
            name="Windows bootstrap",
            owner="admin",
            credential_type="windows",
            username="administrator",
        )
        windows.set_credential_data({"password": "secret"})
        db.session.add_all([linux, windows])
        db.session.commit()

        package = ensure_builtin_admin_automation()["package"]
        bootstrap_input = next(
            item for item in package.inputs
            if item.variable_name == "journeyman_bootstrap_credential_id"
        )
        assert bootstrap_input.input_type == "choice"
        assert bootstrap_input.get_choices() == [
            {"value": linux.id, "label": "src_ansibilion"}
        ]


def test_builtin_backup_automation_is_seeded_disabled(app):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()

        project = Project.query.filter_by(builtin_key=BACKUP_BUILTIN_KEY).one()
        package = ProjectPackage.query.filter_by(builtin_key=BACKUP_BUILTIN_KEY).one()
        schedule = ProjectSchedule.query.filter_by(
            project_id=project.id,
            name=BACKUP_SCHEDULE_NAME,
        ).one()

        assert project.name == BACKUP_PROJECT_NAME
        assert package.name == BACKUP_PACKAGE_NAME
        assert project.owner == BUILTIN_OWNER
        assert package.owner == BUILTIN_OWNER
        assert package.access_mode == "restricted"
        assert package.permissions == []
        assert package.get_fixed_vars() == {"var_action": "backup"}
        assert len(package.inputs) == 1
        assert package.inputs[0].variable_name == "path"
        assert package.inputs[0].get_default_value() == "/tmp"
        assert len(project.steps) == 1
        assert project.steps[0].playbook == "journeyman-backup-restore.yml"
        assert project.runner_routing == "local"

        assert schedule.name.startswith("ZZ - ")
        assert schedule.schedule_type == "daily"
        assert schedule.timezone_name == "UTC"
        assert schedule.start_at.hour == 5
        assert schedule.enabled is False
        assert schedule.next_run_at is None
        assert schedule.created_by == BUILTIN_OWNER

        checkout = Path(app.config["REPOSITORY_ROOT"]) / str(
            seeded["repository"].id
        )
        playbook = (checkout / "journeyman-backup-restore.yml").read_text()
        assert "var_action: backup" in playbook
        assert "path: /tmp" in playbook
        assert "ansible_connection: ssh" in playbook
        assert "ansible_host: 127.0.0.1" in playbook


def test_builtin_seed_is_idempotent(app):
    with app.app_context():
        first = ensure_builtin_admin_automation()
        first_ids = (first["project"].id, first["package"].id, first["repository"].id)
        second = ensure_builtin_admin_automation()
        second_ids = (second["project"].id, second["package"].id, second["repository"].id)
        assert second_ids == first_ids
        assert Project.query.filter_by(name=REMOTE_RUNNER_PROJECT_NAME).count() == 1
        assert ProjectPackage.query.filter_by(name=REMOTE_RUNNER_PACKAGE_NAME).count() == 1
        assert Project.query.filter_by(name=BACKUP_PROJECT_NAME).count() == 1
        assert ProjectPackage.query.filter_by(name=BACKUP_PACKAGE_NAME).count() == 1
        backup_project = Project.query.filter_by(name=BACKUP_PROJECT_NAME).one()
        assert ProjectSchedule.query.filter_by(
            project_id=backup_project.id,
            name=BACKUP_SCHEDULE_NAME,
        ).count() == 1


def test_builtin_seed_can_resequence_inputs_from_older_definition(app):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()
        package = seeded["package"]

        # Simulate the older Manage Remote Runner Package layout that existed
        # before runner name/site/concurrency and pip-proxy inputs were added.
        keep_positions = {
            "journeyman_manage_action": 1,
            "journeyman_runner_host": 2,
            "journeyman_server_url": 3,
            "journeyman_remove_runner_software": 4,
        }

        for temporary_position, package_input in enumerate(
            list(package.inputs), start=1
        ):
            package_input.position = 2000000 + temporary_position
        db.session.flush()

        for package_input in list(package.inputs):
            if package_input.variable_name not in keep_positions:
                package.inputs.remove(package_input)
                db.session.delete(package_input)
        db.session.flush()

        for package_input in package.inputs:
            package_input.position = keep_positions[package_input.variable_name]
        db.session.commit()

        refreshed = ensure_builtin_admin_automation()["package"]
        assert [
            (item.position, item.variable_name)
            for item in refreshed.inputs
        ] == [
            (1, "journeyman_manage_action"),
            (2, "journeyman_runner_host"),
            (3, "journeyman_runner_name"),
            (4, "journeyman_bootstrap_credential_id"),
            (5, "journeyman_runner_site"),
            (6, "journeyman_runner_max_concurrent_steps"),
            (7, "journeyman_pip_proxy_required"),
            (8, "journeyman_pip_proxy_credential_id"),
            (9, "journeyman_remove_runner_software"),
        ]


def test_non_admin_projects_page_hides_builtin(client, app):
    with app.app_context():
        ensure_builtin_admin_automation()
    response = client.get(
        "/projects",
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 200
    assert REMOTE_RUNNER_PROJECT_NAME.encode() not in response.data


def test_builtin_runner_management_cleans_remote_before_control_plane_when_requested(app):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()
        checkout = Path(app.config["REPOSITORY_ROOT"]) / str(seeded["repository"].id)
        playbook = (checkout / "manage-remote-runner.yml").read_text()

        preflight = playbook.index(
            "Preflight unregister/delete before optional remote cleanup"
        )
        ssh_cleanup = playbook.index("Verify SSH access for optional remote cleanup")
        finalise = playbook.index(
            "Apply unregister/delete after successful remote cleanup"
        )
        assert preflight < ssh_cleanup < finalise
        assert "Apply unregister/delete immediately when no remote cleanup is requested" in playbook
        assert "/opt/journeyman/bin/journeyman-runner-admin" in playbook
        assert "Check remote runner configuration" not in playbook
        assert "Prepare runner record and one-time token for installation" in playbook
        assert "Refuse localhost or the Journeyman control-plane node" in playbook
        assert "Proxy required for pip" not in playbook
        assert "journeyman_pip_proxy_url" in playbook
        assert "journeyman_pip_proxy_credential_id" in playbook
        assert "Wait for runner registration and heartbeat" in playbook

def test_builtin_repository_refresh_uses_builtin_materialisation(app, client, monkeypatch):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()
        repository_id = seeded["repository"].id

    def unexpected_git_sync(*args, **kwargs):
        raise AssertionError("Built-in repository must not use normal Git sync.")

    monkeypatch.setattr(
        "app.views.repositories.sync_repository",
        unexpected_git_sync,
    )

    response = client.post(
        f"/repositories/{repository_id}/sync",
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"refreshed from the installed Journeyman files" in response.data

    with app.app_context():
        repository = db.session.get(Repository, repository_id)
        assert repository.name == BUILTIN_REPOSITORY_NAME
        assert repository.status == "up_to_date"
        assert repository.last_sync_message == (
            "Journeyman built-in automation is current."
        )


def test_builtin_repository_page_labels_sync_as_refresh(app, client):
    with app.app_context():
        seeded = ensure_builtin_admin_automation()
        repository_id = seeded["repository"].id

    response = client.get(
        f"/repositories?selected={repository_id}",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert b"Refresh Built-in Automation" in response.data
