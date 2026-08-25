from pathlib import Path

from app import db
from app.models import Credential, Inventory, Project, ProjectPackage, ReleaseTestSetting
from app.services.builtin_automation import (
    RELEASE_TEST_BUILTIN_KEY,
    RELEASE_TEST_FAILURE_BUILTIN_KEY,
    ensure_builtin_release_validation,
)
from app.services.release_testing import (
    EXPECTED_PARTIAL_FAILURE_MARKER,
    evaluate_validation_job,
    get_or_create_release_test_settings,
)


def identity_headers(username):
    return {"X-Test-Username": username}


def _seed_targets():
    inventory = Inventory(
        name="Release test Linux",
        inventory_type="static",
        endpoint="",
        enabled=True,
        status="up_to_date",
        config_json='{"content":"all:\\n  hosts:\\n    test01:\\n"}',
    )
    credential = Credential(
        name="Release test machine",
        owner="admin",
        credential_type="machine",
        username="svc_test",
    )
    credential.set_credential_data(
        {
            "password": "not-a-real-password",
            "become_method": "sudo",
            "become_user": "root",
        }
    )
    db.session.add_all([inventory, credential])
    db.session.commit()
    return inventory, credential


def test_non_admin_cannot_view_release_testing_settings(client):
    response = client.get(
        "/settings/release-testing",
        headers=identity_headers("alice"),
    )
    assert response.status_code == 403

def test_release_testing_inventory_dropdown_uses_standard_name_ordering(app, client):
    with app.app_context():
        db.session.add_all(
            [
                Inventory(
                    name="Zulu inventory",
                    inventory_type="static",
                    endpoint="",
                    enabled=True,
                    status="up_to_date",
                    config_json='{"content":"all:\n  hosts:\n    zulu:\n"}',
                ),
                Inventory(
                    name="alpha inventory",
                    inventory_type="static",
                    endpoint="",
                    enabled=True,
                    status="up_to_date",
                    config_json='{"content":"all:\n  hosts:\n    alpha:\n"}',
                ),
                Inventory(
                    name="ZZ - Built-in inventory",
                    inventory_type="static",
                    endpoint="",
                    enabled=True,
                    status="up_to_date",
                    config_json='{"content":"all:\n  hosts:\n    builtin:\n"}',
                ),
            ]
        )
        db.session.commit()

    response = client.get(
        "/settings/release-testing",
        headers=identity_headers("admin"),
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert text.index("alpha inventory") < text.index("Zulu inventory")
    assert text.index("Zulu inventory") < text.index("ZZ - Built-in inventory")

def test_admin_can_save_release_testing_settings_and_seed_validation(app, client):
    with app.app_context():
        inventory, credential = _seed_targets()
        inventory_id = inventory.id
        credential_id = credential.id

    response = client.post(
        "/settings/release-testing",
        data={
            "inventory_id": str(inventory_id),
            "credential_id": str(credential_id),
            "runner_crew_id": "",
            "host_pattern": "test01,test02",
            "alternate_become_users": "oracle\nenmac\noracle",
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(ReleaseTestSetting, 1)
        assert settings.inventory_id == inventory_id
        assert settings.credential_id == credential_id
        assert settings.host_pattern == "test01,test02"
        assert settings.become_users() == ["oracle", "enmac"]

        project = Project.query.filter_by(builtin_key=RELEASE_TEST_BUILTIN_KEY).one()
        package = ProjectPackage.query.filter_by(builtin_key=RELEASE_TEST_BUILTIN_KEY).one()
        failure_project = Project.query.filter_by(
            builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY
        ).one()
        failure_package = ProjectPackage.query.filter_by(
            builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY
        ).one()
        assert project.inventory_id == inventory_id
        assert [row.id for row in project.credentials] == [credential_id]
        assert project.steps[0].playbook == "release_linux_validation.yml"
        assert package.inputs[0].binding_type == "step_limit"
        assert package.inputs[0].get_default_value() == "test01,test02"
        assert package.get_fixed_vars()["journeyman_release_test_expected_login_user"] == "svc_test"
        assert package.get_fixed_vars()["journeyman_release_test_expected_become_user"] == "root"
        assert package.get_fixed_vars()["journeyman_release_test_become_users"] == ["oracle", "enmac"]
        assert failure_project.steps[0].playbook == "release_linux_partial_failure.yml"
        assert failure_package.inputs[0].binding_type == "step_limit"
        assert failure_package.inputs[0].get_default_value() == "test01,test02"
        assert failure_package.inputs[1].variable_name == "journeyman_release_test_failure_host"
        assert failure_package.inputs[1].required is True

        checkout = Path(app.config["REPOSITORY_ROOT"]) / str(project.repository_id)
        assert (checkout / "release_linux_validation.yml").is_file()
        assert (checkout / "linux_connectivity.yml").is_file()
        assert (checkout / "linux_become.yml").is_file()
        assert (checkout / "linux_runtime_variables.yml").is_file()
        assert (checkout / "release_linux_partial_failure.yml").is_file()


def test_release_testing_page_shows_run_button_when_configured(app, client):
    with app.app_context():
        inventory, credential = _seed_targets()
        settings = get_or_create_release_test_settings()
        settings.inventory = inventory
        settings.credential = credential
        settings.host_pattern = "test01"
        db.session.commit()
        seeded = ensure_builtin_release_validation(settings)
        package_id = seeded["package"].id

    response = client.get(
        "/settings/release-testing",
        headers=identity_headers("admin"),
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Run Validation Suite" in text
    assert f"/packages/{package_id}/launch" in text
    assert "Run Expected Failure Test" in text


def test_release_validation_playbooks_are_harmless_identity_checks():
    root = Path(__file__).resolve().parent / "playbooks"
    aggregate = (root / "release_linux_validation.yml").read_text(encoding="utf-8")
    become = (root / "linux_become.yml").read_text(encoding="utf-8")
    connectivity = (root / "linux_connectivity.yml").read_text(encoding="utf-8")
    partial_failure = (root / "release_linux_partial_failure.yml").read_text(encoding="utf-8")

    assert "linux_connectivity.yml" in aggregate
    assert "linux_become.yml" in aggregate
    assert "linux_runtime_variables.yml" in aggregate
    assert "ansible.builtin.raw: id -un" in connectivity
    assert EXPECTED_PARTIAL_FAILURE_MARKER in partial_failure
    assert "ansible.builtin.fail:" in partial_failure
    assert "become_user: \"{{ item }}\"" in become
    for forbidden in ("package:", "service:", "reboot:", "file:", "shell:"):
        assert forbidden not in connectivity
        assert forbidden not in become


def test_expected_failure_evaluator_requires_job_step_slice_and_marker():
    from types import SimpleNamespace

    slice_result = SimpleNamespace(status="failed", host_count=3, stdout=EXPECTED_PARTIAL_FAILURE_MARKER)
    step = SimpleNamespace(status="failed", stdout="", execution_slices=[slice_result])
    job = SimpleNamespace(id=42, status="failed", steps=[step])

    result = evaluate_validation_job(job, expected_failure=True)
    assert result["passed"] is True
    assert result["multi_host_slice"] is True
    assert result["max_slice_hosts"] == 3

    no_marker = SimpleNamespace(
        id=43,
        status="failed",
        steps=[SimpleNamespace(status="failed", stdout="", execution_slices=[SimpleNamespace(status="failed", host_count=3, stdout="")])],
    )
    assert evaluate_validation_job(no_marker, expected_failure=True)["passed"] is False


def test_success_evaluator_reports_multi_host_slice():
    from types import SimpleNamespace

    job = SimpleNamespace(
        id=44,
        status="successful",
        steps=[SimpleNamespace(status="successful", execution_slices=[SimpleNamespace(status="successful", host_count=4)])],
    )
    result = evaluate_validation_job(job)
    assert result["passed"] is True
    assert result["multi_host_slice"] is True
    assert result["max_slice_hosts"] == 4
