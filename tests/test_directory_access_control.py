from types import SimpleNamespace

import pytest

from app import db
import app.routes as routes
from app.models import (
    DirectorySetting,
    ProjectPackage,
    Team,
)
from app.services.directory_settings import (
    DirectorySettingsValidationError,
    default_directory_settings_values,
    validate_directory_settings,
)


def identity_headers(username):
    return {
        "X-Test-Username": username,
    }


def valid_directory_values():
    return {
        "enabled": True,
        "base_dn": (
            "DC=example,DC=com"
        ),
        "user_search_base": (
            "OU=Users,DC=example,DC=com"
        ),
        "group_search_base": (
            "OU=Groups,DC=example,DC=com"
        ),
        "bind_username": (
            "svc_journeyman@example.com"
        ),
        "bind_password": "directory-secret",
        "ca_certificate_path": (
            "/etc/pki/ca-trust/source/anchors/ad-ca.pem"
        ),
        "connect_timeout_seconds": "3",
        "operation_timeout_seconds": "10",
        "administrator_group_name": (
            "Journeyman Admins"
        ),
        "user_group_name": "Journeyman Users",
        "include_nested_groups": True,
        "servers": [
            {
                "host": "dc01.example.com",
                "port": "636",
                "use_ssl": True,
                "enabled": True,
            },
            {
                "host": "dc02.example.com",
                "port": "636",
                "use_ssl": True,
                "enabled": True,
            },
        ],
    }


def directory_form_data(*, bind_password="directory-secret"):
    return {
        "enabled": "on",
        "base_dn": (
            "DC=example,DC=com"
        ),
        "user_search_base": (
            "OU=Users,DC=example,DC=com"
        ),
        "group_search_base": (
            "OU=Groups,DC=example,DC=com"
        ),
        "bind_username": (
            "svc_journeyman@example.com"
        ),
        "bind_password": bind_password,
        "ca_certificate_path": (
            "/etc/pki/ca-trust/source/anchors/ad-ca.pem"
        ),
        "connect_timeout_seconds": "3",
        "operation_timeout_seconds": "10",
        "administrator_group_name": (
            "Journeyman Admins"
        ),
        "user_group_name": "Journeyman Users",
        "include_nested_groups": "on",
        "server_host": [
            "dc01.example.com",
            "dc02.example.com",
        ],
        "server_port": ["636", "636"],
        "server_use_ssl": ["1", "2"],
        "server_enabled": ["1", "2"],
    }



def test_directory_role_group_defaults_can_be_configured(app):
    app.config["DIRECTORY_ADMIN_GROUP_NAME"] = "Automation Admins"
    app.config["DIRECTORY_USER_GROUP_NAME"] = "Automation Users"

    with app.app_context():
        values = default_directory_settings_values()

    assert values["administrator_group_name"] == "Automation Admins"
    assert values["user_group_name"] == "Automation Users"

def test_directory_requires_two_enabled_servers(app):
    values = valid_directory_values()
    values["servers"] = values["servers"][:1]

    with app.app_context():
        with pytest.raises(
            DirectorySettingsValidationError
        ) as exc_info:
            validate_directory_settings(values)

    assert "At least two enabled directory servers" in str(
        exc_info.value
    )


def test_directory_rejects_plain_ldap(app):
    values = valid_directory_values()
    values["servers"][0]["use_ssl"] = False

    with app.app_context():
        with pytest.raises(
            DirectorySettingsValidationError
        ) as exc_info:
            validate_directory_settings(values)

    assert "must use LDAPS" in str(exc_info.value)


def test_non_admin_cannot_view_directory_pages(client):
    for path in (
        "/settings/directory",
        "/users",
        "/teams",
    ):
        response = client.get(
            path,
            headers=identity_headers("alice"),
        )
        assert response.status_code == 403


def test_admin_can_save_encrypted_directory_settings(
    app,
    client,
):
    response = client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(
            DirectorySetting,
            1,
        )

        assert settings is not None
        assert settings.enabled
        assert settings.get_bind_password() == (
            "directory-secret"
        )
        assert settings.encrypted_bind_password != (
            b"directory-secret"
        )
        assert [
            server.host
            for server in settings.servers
        ] == [
            "dc01.example.com",
            "dc02.example.com",
        ]
        assert settings.administrator_group_name == (
            "Journeyman Admins"
        )
        assert settings.user_group_name == (
            "Journeyman Users"
        )


def test_directory_server_test_strips_nul_from_ldap_error(
    app,
    client,
    monkeypatch,
):
    client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
    )

    with app.app_context():
        settings = db.session.get(DirectorySetting, 1)
        servers = list(settings.servers)

    fake_results = [
        {
            "server": server,
            "ok": False,
            "message": (
                "LDAPInvalidCredentialsResult - 49 - "
                "invalidCredentials - data 52e\x00"
            ),
        }
        for server in servers
    ]
    fake_client = SimpleNamespace(
        test_servers=lambda: fake_results,
    )

    monkeypatch.setattr(
        routes,
        "get_directory_client",
        lambda settings: fake_client,
    )

    response = client.post(
        "/settings/directory/test",
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(DirectorySetting, 1)
        assert all(
            server.last_test_ok is False
            for server in settings.servers
        )
        assert all(
            "\x00" not in server.last_test_message
            for server in settings.servers
        )
        assert all(
            "data 52e" in server.last_test_message
            for server in settings.servers
        )

def test_blank_bind_password_preserves_existing_secret(
    app,
    client,
):
    first_response = client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
    )
    assert first_response.status_code == 302

    second_response = client.post(
        "/settings/directory",
        data=directory_form_data(bind_password=""),
        headers=identity_headers("admin"),
    )
    assert second_response.status_code == 302

    with app.app_context():
        settings = db.session.get(
            DirectorySetting,
            1,
        )
        assert settings.get_bind_password() == (
            "directory-secret"
        )


def test_users_page_no_longer_reads_authorization_from_directory(
    client,
):
    response = client.get(
        "/users",
        headers=identity_headers("admin"),
    )

    assert response.status_code == 200
    assert b"Journeyman-local roles and rights" in response.data


def test_admin_can_create_local_team_with_local_members(
    app,
    client,
):
    from app.models import AuthorizationRole, UserAccount
    from app.services.authorization import ROLE_USER

    with app.app_context():
        user_role = AuthorizationRole.query.filter_by(name=ROLE_USER).one()
        account = UserAccount(
            username="alice",
            display_name="Alice Example",
            enabled=True,
        )
        db.session.add(account)
        account.roles.append(user_role)
        db.session.commit()
        user_id = account.id

    response = client.post(
        "/teams/new",
        data={
            "display_name": "Network Operations",
            "description": "Network operations team.",
            "member_ids": [str(user_id)],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        team = Team.query.one()
        assert team.display_name == "Network Operations"
        assert team.source_kind == "local"
        assert team.created_by == "admin"
        assert [member.username for member in team.members] == ["alice"]


def test_local_permission_validation_rejects_free_text():
    from app.services.project_package_permissions import (
        validate_package_permission_rows,
    )

    errors, rows = validate_package_permission_rows(
        [{"principal_key": "legacy|user|made.up.user"}],
        allowed_principals={},
    )

    assert errors
    assert "eligible Journeyman User or Team" in errors[0]
    assert rows[0]["principal_name"] == ""


def test_local_permission_uses_canonical_local_reference():
    from app.services.project_package_permissions import (
        validate_package_permission_rows,
    )

    key = "user|7"
    canonical = {
        "principal_type": "user",
        "principal_name": "alice",
        "principal_object_guid": None,
        "principal_dn": "",
        "user_account_id": 7,
        "team_id": None,
    }

    errors, rows = validate_package_permission_rows(
        [{"principal_key": key}],
        allowed_principals={key: canonical},
    )

    assert errors == []
    assert rows == [canonical]


def test_package_team_permission_is_selected_from_local_team(
    app,
    client,
    seeded_packages,
):
    with app.app_context():
        team = Team.new_local(
            display_name="Automation Support",
            description="",
            created_by="admin",
        )
        db.session.add(team)
        db.session.commit()
        team_id = team.id

    response = client.post(
        "/packages/new",
        data={
            "name": "Team Permission Package",
            "description": "",
            "project_id": str(seeded_packages["enabled_project"]),
            "enabled": "on",
            "access_mode": "restricted",
            "warning_message": "",
            "confirmation_required": "on",
            "confirmation_message": "",
            "fixed_vars_yaml": "{}",
            "package_permission_row": ["1"],
            "package_permission_1_principal_key": "team|{}".format(team_id),
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        package = ProjectPackage.query.filter_by(
            name="Team Permission Package"
        ).one()
        assert len(package.permissions) == 1
        permission = package.permissions[0]
        assert permission.principal_type == "group"
        assert permission.principal_name == "Automation Support"
        assert permission.team_id == team_id


def test_admin_and_user_role_groups_must_differ(app):
    values = valid_directory_values()
    values["user_group_name"] = "journeyman admins"

    with app.app_context():
        with pytest.raises(
            DirectorySettingsValidationError
        ) as exc_info:
            validate_directory_settings(values)

    assert "must be different" in str(exc_info.value)


def test_package_grant_summary_skips_orphaned_permissions():
    """Stale permission rows must never crash the Users/Teams pages."""
    valid_permission = SimpleNamespace(
        user_account_id=4,
        team_id=None,
        package=SimpleNamespace(name="Valid Package"),
    )
    team_permission = SimpleNamespace(
        user_account_id=None,
        team_id=7,
        package=SimpleNamespace(name="Team Package"),
    )
    orphaned_permission = SimpleNamespace(
        user_account_id=5,
        team_id=None,
        package=None,
    )

    grants = routes._package_grants_by_local_principal(
        [valid_permission, team_permission, orphaned_permission]
    )

    assert grants == {
        "users": {4: ["Valid Package"]},
        "teams": {7: ["Team Package"]},
    }
