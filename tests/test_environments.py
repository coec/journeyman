from pathlib import Path

import pytest

from app import db
from app.models.environment import Environment
import app.routes as routes


def test_environments_page_is_admin_only(client):
    response = client.get("/environments", headers={"X-Test-Username": "ordinary"})
    assert response.status_code == 403


def test_environments_page_marks_effective_default_next_to_name(app, client, monkeypatch):
    from app.services import environments as environment_service

    monkeypatch.setattr(environment_service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(environment_service, "_run_version", lambda command: "version ok")

    response = client.get("/environments", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    assert b"<strong>System Ansible</strong> <span class=\"muted\">(default)</span>" in response.data
    assert b"<small>Built-in execution environment</small>" in response.data
    assert b"<strong>Journeyman application environment</strong><br>" in response.data
    assert b"<small>Journeyman application runtime</small>" in response.data


def test_environments_page_moves_default_marker_to_managed_environment(app, client, monkeypatch):
    from app.services import environments as environment_service

    monkeypatch.setattr(environment_service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(environment_service, "_run_version", lambda command: "version ok")

    with app.app_context():
        environment_service.ensure_builtin_environment()
        Environment.query.update({Environment.is_default: False})
        managed = Environment(
            name="My Environment",
            path="/opt/journeyman/environments/my-environment",
            enabled=True,
            is_default=True,
            is_managed=True,
            validation_status="passed",
            build_status="passed",
        )
        db.session.add(managed)
        db.session.commit()

    response = client.get("/environments", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    assert b"<strong>My Environment</strong> <span class=\"muted\">(default)</span>" in response.data
    assert b"<strong>System Ansible</strong><br>" in response.data
    assert response.data.index(b"<strong>Journeyman application environment</strong>") < response.data.index(
        b"<strong>My Environment</strong>"
    ) < response.data.index(b"<strong>System Ansible</strong>")


def test_admin_can_register_managed_environment(app, client, monkeypatch, tmp_path):
    managed_root = tmp_path / "managed-environments"
    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(managed_root)
    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"

    def fake_prepare(environment, **kwargs):
        environment.build_status = "queued"
        environment.validation_status = "not_tested"
        db.session.commit()
        return environment

    monkeypatch.setattr(routes, "prepare_managed_environment_build", fake_prepare)

    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Network Automation",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core==2.19.0",
            "pip_requirements": "jmespath\ndnspython==2.7.0",
            "collections": "ansible.netcommon\ncisco.ios:11.0.0",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        environment = Environment.query.filter_by(name="Network Automation").one()
        assert environment.is_managed is True
        assert Path(environment.path) == managed_root / "network-automation"
        assert environment.build_status == "queued"


def test_managed_environment_rejects_duplicate_name(app, client):
    with app.app_context():
        db.session.add(Environment(name="Duplicate", path="/tmp/existing"))
        db.session.commit()

    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Duplicate",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"already exists" in response.data


def test_system_ansible_becomes_default_when_application_environment_was_default(app, monkeypatch):
    from app.services import environments as environment_service

    monkeypatch.setattr(environment_service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(environment_service, "_run_version", lambda command: "version ok")

    with app.app_context():
        application_environment = Environment(
            name="Journeyman application environment",
            path="/opt/journeyman/venv314",
            enabled=True,
            is_default=True,
            is_builtin=True,
            validation_status="failed",
        )
        db.session.add(application_environment)
        db.session.commit()

        system_environment = environment_service.ensure_builtin_environment()

        assert system_environment.name == "System Ansible"
        assert system_environment.path == "__SYSTEM_ANSIBLE__"
        assert system_environment.is_default is True
        assert system_environment.validation_status == "passed"
        assert application_environment.is_default is False


def test_admin_can_queue_managed_environment_rebuild(app, client, monkeypatch):
    with app.app_context():
        environment = Environment(name="Cisco", path="/tmp/cisco", is_managed=True, python_interpreter="/usr/bin/python3", ansible_spec="ansible-core")
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    def fake_prepare(environment, **kwargs):
        environment.build_status = "queued"
        environment.ansible_spec = kwargs["ansible_spec"]
        db.session.commit()
        return environment

    monkeypatch.setattr(routes, "prepare_managed_environment_build", fake_prepare)
    response = client.post(
        f"/environments/{environment_id}/edit",
        headers={"X-Test-Username": "admin"},
        data={"name": "Cisco", "python_interpreter": "/usr/bin/python3", "ansible_spec": "ansible-core>=2.20", "pip_requirements": "jmespath", "collections": "cisco.ios"},
    )
    assert response.status_code == 302
    with app.app_context():
        environment = db.session.get(Environment, environment_id)
        assert environment.build_status == "queued"
        assert environment.ansible_spec == "ansible-core>=2.20"


def test_managed_environment_stores_ansible_config_path(app, client, monkeypatch, tmp_path):
    managed_root = tmp_path / "managed-environments"
    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(managed_root)
    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"

    def fake_prepare(environment, **kwargs):
        environment.build_status = "queued"
        db.session.commit()
        return environment

    monkeypatch.setattr(routes, "prepare_managed_environment_build", fake_prepare)
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Configured Ansible",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "ansible_config_path": "/etc/ansible/network.cfg",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        environment = Environment.query.filter_by(name="Configured Ansible").one()
        assert environment.ansible_config_path == "/etc/ansible/network.cfg"


@pytest.mark.security
def test_environment_rejects_relative_ansible_config_path(client):
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Bad config path",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "ansible_config_path": "ansible.cfg",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Ansible configuration path must be an absolute path" in response.data


@pytest.mark.security
def test_environment_rejects_shell_metacharacters_in_ansible_config_path(client):
    payload = "/etc/ansible/ansible.cfg; touch /tmp/I_GOT_HACKED"
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Bobby config",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "ansible_config_path": payload,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"contains unsupported characters" in response.data


@pytest.mark.security
def test_environment_rejects_parent_traversal_in_ansible_config_path(client):
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Traversal config",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "ansible_config_path": "/etc/ansible/../secrets.cfg",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"must not contain" in response.data
    assert b"components" in response.data


@pytest.mark.security
def test_environment_rejects_non_cfg_ansible_config_path(client):
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Wrong suffix",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "ansible_config_path": "/etc/ansible/ansible.conf",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"must end in .cfg" in response.data


def test_environments_page_only_lists_environments(client):
    response = client.get("/environments", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200
    assert b"Add Environment" in response.data
    assert b"Create managed environment" not in response.data
    assert b"Register existing virtual environment" not in response.data


def test_new_environment_page_contains_both_create_forms(client):
    response = client.get("/environments/new", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200
    assert b"Create managed environment" in response.data
    assert b"Register existing virtual environment" in response.data
    assert b"Additional Python packages" in response.data
    assert b"Ansible collections" in response.data
    assert b"Absolute path" in response.data


def test_managed_environment_edit_uses_managed_create_fields(app, client):
    with app.app_context():
        environment = Environment(
            name="Modern Ansible",
            path="/tmp/modern-ansible",
            is_managed=True,
            python_interpreter="/usr/bin/python3",
            ansible_spec="ansible-core>=2.20",
            ansible_config_path="/etc/ansible/modern.cfg",
            pip_requirements="jmespath",
            collection_requirements="community.general",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    response = client.get(
        f"/environments/{environment_id}/edit",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    assert b"Python interpreter" in response.data
    assert b"Ansible package specification" in response.data
    assert b"Ansible configuration file" in response.data
    assert b"Additional Python packages" in response.data
    assert b"Ansible collections" in response.data
    assert b"ansible-core&gt;=2.20" in response.data
    assert b"jmespath" in response.data
    assert b"community.general" in response.data


def test_external_environment_edit_uses_registration_fields(app, client):
    with app.app_context():
        environment = Environment(
            name="External",
            path="/opt/automation/venvs/external",
            ansible_config_path="/etc/ansible/external.cfg",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    response = client.get(
        f"/environments/{environment_id}/edit",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    assert b"Absolute path" in response.data
    assert b"Ansible configuration file" in response.data
    assert b"Additional Python packages" in response.data
    assert b"Ansible collections" in response.data
    assert b"/opt/automation/venvs/external" in response.data
    assert b"Python interpreter" not in response.data


def test_external_environment_edit_can_queue_python_packages_and_collections(app, client, monkeypatch, tmp_path):
    root = tmp_path / "external"
    (root / "bin").mkdir(parents=True)
    for name in ("python", "ansible-playbook", "ansible-galaxy"):
        path = root / "bin" / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    with app.app_context():
        environment = Environment(
            name="External",
            path=str(root),
            ansible_config_path="/etc/ansible/external.cfg",
            validation_status="passed",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    monkeypatch.setattr(routes, "validate_environment", lambda environment: True)

    response = client.post(
        f"/environments/{environment_id}/edit",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "External",
            "path": str(root),
            "ansible_config_path": "/etc/ansible/external.cfg",
            "pip_requirements": "jmespath\ndnspython==2.7.0",
            "collections": "community.general\ncisco.ios",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        environment = db.session.get(Environment, environment_id)
        assert environment.pip_requirements == "jmespath\ndnspython==2.7.0"
        assert environment.collection_requirements == "community.general\ncisco.ios"
        assert environment.build_status == "queued"


def _url_proxy_credential():
    from app.models import Credential
    from app.credential_types import CREDENTIAL_TYPE_URL

    credential = Credential(
        name="Build Proxy",
        owner="admin",
        credential_type=CREDENTIAL_TYPE_URL,
        username="proxyuser",
    )
    credential.set_credential_data(
        {
            "url": "http://proxy.example.org:8080",
            "auth_mode": "basic",
            "password": "proxy-secret",
            "token": "",
            "token_prefix": "",
            "token_url": "",
            "scope": "",
        }
    )
    db.session.add(credential)
    db.session.commit()
    return credential


def test_environment_forms_offer_url_credentials_as_build_proxy(app, client):
    with app.app_context():
        credential = _url_proxy_credential()
        environment = Environment(
            name="Proxy Test",
            path="/tmp/proxy-test",
            is_managed=True,
            python_interpreter="/usr/bin/python3",
            ansible_spec="ansible-core",
            proxy_credential_id=credential.id,
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id
        credential_id = credential.id

    response = client.get("/environments/new", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200
    assert b"Build proxy credential (optional)" in response.data
    assert b"Build Proxy" in response.data

    response = client.get(
        f"/environments/{environment_id}/edit",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    assert b'value="' + str(credential.id).encode() + b'" selected' in response.data


def test_environment_build_proxy_credential_overrides_global_proxy(app):
    from app.services.environment_build_settings import build_proxy_environment

    with app.app_context():
        credential = _url_proxy_credential()
        result = build_proxy_environment(
            {"PATH": "/usr/bin"},
            proxy_credential=credential,
        )

    assert result["HTTPS_PROXY"] == "http://proxyuser:proxy-secret@proxy.example.org:8080"
    assert result["HTTP_PROXY"] == result["HTTPS_PROXY"]


def test_environment_rejects_non_proxy_url_auth_mode(app, client, monkeypatch, tmp_path):
    from app.models import Credential
    from app.credential_types import CREDENTIAL_TYPE_URL

    managed_root = tmp_path / "managed-environments"
    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(managed_root)
    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"

    with app.app_context():
        credential = Credential(
            name="Bearer API",
            owner="admin",
            credential_type=CREDENTIAL_TYPE_URL,
            username="",
        )
        credential.set_credential_data(
            {
                "url": "https://api.example.org",
                "auth_mode": "bearer",
                "token": "secret",
                "token_prefix": "Bearer",
                "password": "",
                "token_url": "",
                "scope": "",
            }
        )
        db.session.add(credential)
        db.session.commit()
        credential_id = credential.id

    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Bad Proxy",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "proxy_credential_id": str(credential_id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"must use None or HTTP Basic authentication" in response.data


def test_environment_proxy_credential_is_persisted_on_managed_create(app, client, monkeypatch, tmp_path):
    managed_root = tmp_path / "managed-environments"
    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(managed_root)
    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"

    with app.app_context():
        credential = _url_proxy_credential()
        credential_id = credential.id

    def fake_prepare(environment, **kwargs):
        environment.build_status = "queued"
        db.session.commit()
        return environment

    monkeypatch.setattr(routes, "prepare_managed_environment_build", fake_prepare)
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Proxy Managed",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "proxy_credential_id": str(credential_id),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        environment = Environment.query.filter_by(name="Proxy Managed").one()
        assert environment.proxy_credential_id == credential_id


def test_application_runtime_validation_does_not_require_ansible_playbook(app, monkeypatch, tmp_path):
    from app.services import environments as environment_service

    runtime = tmp_path / "application-runtime"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    monkeypatch.setattr(environment_service, "_run_version", lambda command: "Python 3.14.5")

    with app.app_context():
        environment = Environment(
            name=environment_service.APPLICATION_ENVIRONMENT_NAME,
            path=str(runtime),
            enabled=True,
            is_builtin=True,
        )
        db.session.add(environment)
        db.session.commit()

        assert environment_service.validate_environment(environment) is True
        assert environment.validation_status == "passed"
        assert environment.ansible_version == ""
        assert "application Python" in environment.validation_message


def test_managed_environment_stores_runner_system_packages(app, client, monkeypatch, tmp_path):
    managed_root = tmp_path / "managed-environments"
    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(managed_root)
    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"

    def fake_prepare(environment, **kwargs):
        environment.system_requirements = kwargs["system_requirements"]
        environment.build_status = "queued"
        db.session.commit()
        return environment

    monkeypatch.setattr(routes, "prepare_managed_environment_build", fake_prepare)
    response = client.post(
        "/environments/create",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "System package environment",
            "python_interpreter": "/usr/bin/python3",
            "ansible_spec": "ansible-core",
            "system_requirements": "adcli\nkrb5-workstation",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        environment = Environment.query.filter_by(name="System package environment").one()
        assert environment.system_requirements == "adcli\nkrb5-workstation"


@pytest.mark.security
def test_environment_rejects_unsafe_runner_system_package(app):
    from app.services.environments import prepare_managed_environment_build, EnvironmentBuildError

    app.config["ENVIRONMENT_PYTHON_INTERPRETERS"] = "/usr/bin/python3"
    with app.app_context():
        environment = Environment(
            name="Unsafe system package",
            path="/opt/journeyman/environments/unsafe-system-package",
            is_managed=True,
        )
        db.session.add(environment)
        db.session.flush()
        with pytest.raises(EnvironmentBuildError):
            prepare_managed_environment_build(
                environment,
                python_interpreter="/usr/bin/python3",
                ansible_spec="ansible-core",
                system_requirements="adcli; touch /tmp/nope",
            )


def test_managed_environment_pip_failure_mentions_controller_os_dependencies(app, monkeypatch, tmp_path):
    from app.services import environments as environment_service

    app.config["MANAGED_ENVIRONMENT_ROOT"] = str(tmp_path / "managed-environments")
    final_path = Path(app.config["MANAGED_ENVIRONMENT_ROOT"]) / "pip-failure"

    with app.app_context():
        environment = Environment(
            name="Pip failure",
            path=str(final_path),
            is_managed=True,
            python_interpreter="/usr/bin/python3",
            ansible_spec="ansible-core",
            pip_requirements="pykerberos",
        )
        db.session.add(environment)
        db.session.commit()

        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["/usr/bin/python3", "-m", "venv"]:
                (final_path / "bin").mkdir(parents=True)
                (final_path / "bin" / "python").write_text("")
                raise_if = False
            else:
                raise_if = True
            if raise_if:
                raise environment_service.EnvironmentBuildError("fatal error: krb5.h: No such file")
            return ""

        monkeypatch.setattr(environment_service, "_run", fake_run)

        assert environment_service.create_managed_environment(environment) is False
        assert "fatal error: krb5.h" in environment.build_message
        assert "does not install OS packages on the controller automatically" in environment.build_message
        assert "docs/ENVIRONMENTS.md" in environment.build_message


def test_registered_environment_collection_failure_does_not_show_pip_os_hint(app, monkeypatch, tmp_path):
    from app.services import environments as environment_service

    root = tmp_path / "registered"
    (root / "bin").mkdir(parents=True)
    for name in ("python", "ansible-galaxy"):
        path = root / "bin" / name
        path.write_text("")
        path.chmod(0o755)

    with app.app_context():
        environment = Environment(
            name="Collection failure",
            path=str(root),
            is_managed=False,
            collection_requirements="community.general",
        )
        db.session.add(environment)
        db.session.commit()

        monkeypatch.setattr(
            environment_service,
            "_run",
            lambda command, **kwargs: (_ for _ in ()).throw(
                environment_service.EnvironmentBuildError("Galaxy unavailable")
            ),
        )

        assert environment_service.update_registered_environment(environment) is False
        assert "Galaxy unavailable" in environment.build_message
        assert "does not install OS packages on the controller automatically" not in environment.build_message


def test_managed_environment_validate_hidden_while_building(app, client):
    with app.app_context():
        environment = Environment(
            name="Building Environment",
            path="/tmp/building-environment",
            is_managed=True,
            build_status="building",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    response = client.get("/environments", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    validate_url = f"/environments/{environment_id}/validate".encode()
    assert validate_url not in response.data


def test_managed_environment_validate_rejected_while_queued(app, client, monkeypatch):
    import app.views.environments as environment_views

    with app.app_context():
        environment = Environment(
            name="Queued Environment",
            path="/tmp/queued-environment",
            is_managed=True,
            build_status="queued",
            validation_status="not_tested",
            validation_message="Waiting for build.",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    def unexpected_validate(environment):
        raise AssertionError("validate_environment must not run during a managed build")

    monkeypatch.setattr(environment_views, "validate_environment", unexpected_validate)

    response = client.post(
        f"/environments/{environment_id}/validate",
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Environment validation cannot be run while the environment is being built." in response.data
    with app.app_context():
        environment = db.session.get(Environment, environment_id)
        assert environment.build_status == "queued"
        assert environment.validation_status == "not_tested"
        assert environment.validation_message == "Waiting for build."


def test_managed_environment_validate_still_available_after_failed_build(app, client):
    with app.app_context():
        environment = Environment(
            name="Failed Environment",
            path="/tmp/failed-environment",
            is_managed=True,
            build_status="failed",
        )
        db.session.add(environment)
        db.session.commit()
        environment_id = environment.id

    response = client.get("/environments", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    validate_url = f"/environments/{environment_id}/validate".encode()
    assert validate_url in response.data
