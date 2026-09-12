from app import db
from app.models import DirectorySetting, Project, SystemSetting
from app.models.directory import DIRECTORY_SETTING_ID
from app.models.system_setting import SYSTEM_SETTING_ID
from app.services.approval_workflow import (
    ApprovalWorkflowSettingsError,
    four_eyes_enabled,
    set_four_eyes_enabled,
)
from app.services.project_operational_approval import (
    OPERATIONAL_APPROVAL_REQUIRED_MESSAGE,
    project_operational_approval_error,
)
from app.services.system_settings import get_or_create_system_settings


def identity_headers(username):
    return {"X-Test-Username": username}


def _project():
    project = Project(
        name="4-eyes toggle Project",
        description="Unapproved Project",
        enabled=True,
        owner="admin",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _enable_directory():
    settings = db.session.get(DirectorySetting, DIRECTORY_SETTING_ID)
    if settings is None:
        settings = DirectorySetting(
            id=DIRECTORY_SETTING_ID,
            enabled=True,
            base_dn="dc=example,dc=com",
            user_search_base="dc=example,dc=com",
            group_search_base="dc=example,dc=com",
            bind_username="cn=bind,dc=example,dc=com",
            ca_certificate_path="/etc/pki/ca-trust/source/anchors/test.pem",
            connect_timeout_seconds=3,
            operation_timeout_seconds=10,
            administrator_group_name="Journeyman Admins",
            user_group_name="Journeyman Users",
            include_nested_groups=True,
            updated_by="admin",
        )
        db.session.add(settings)
    else:
        settings.enabled = True
    db.session.flush()
    return settings


def test_four_eyes_is_disabled_by_default(app):
    with app.app_context():
        settings = get_or_create_system_settings()
        assert settings.four_eyes_enabled is False
        assert four_eyes_enabled() is False


def test_cannot_enable_four_eyes_without_directory(app):
    with app.app_context():
        get_or_create_system_settings()

        try:
            set_four_eyes_enabled(True, updated_by="admin")
        except ApprovalWorkflowSettingsError as exc:
            assert "Directory and Authentication" in str(exc)
        else:
            raise AssertionError("4-eyes approval was enabled without Directory authentication")


def test_operational_gate_is_suspended_when_four_eyes_is_off(app):
    with app.app_context():
        get_or_create_system_settings()
        project = _project()

        assert project_operational_approval_error(project) is None


def test_operational_gate_applies_when_four_eyes_is_on(app):
    with app.app_context():
        get_or_create_system_settings()
        _enable_directory()
        set_four_eyes_enabled(True, updated_by="admin")
        project = _project()

        assert (
            project_operational_approval_error(project)
            == OPERATIONAL_APPROVAL_REQUIRED_MESSAGE
        )


def test_admin_can_enable_and_suspend_four_eyes_from_settings(app, client):
    with app.app_context():
        get_or_create_system_settings()
        _enable_directory()
        db.session.commit()

    response = client.post(
        "/settings/approvals",
        data={"four_eyes_enabled": "on"},
        headers=identity_headers("admin"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(SystemSetting, SYSTEM_SETTING_ID)
        assert settings.four_eyes_enabled is True

    response = client.post(
        "/settings/approvals",
        data={},
        headers=identity_headers("admin"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(SystemSetting, SYSTEM_SETTING_ID)
        assert settings.four_eyes_enabled is False


def test_review_route_is_hidden_while_four_eyes_is_off(app, client):
    with app.app_context():
        get_or_create_system_settings()
        project = _project()
        db.session.commit()
        project_id = project.id

    response = client.get(
        "/projects/{}/review".format(project_id),
        headers=identity_headers("admin"),
    )
    assert response.status_code == 404
