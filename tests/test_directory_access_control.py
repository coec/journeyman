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


def test_users_page_reads_role_users_from_directory(
    app,
    client,
    monkeypatch,
):
    client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
    )

    fake_users = [
        SimpleNamespace(
            object_guid=(
                "11111111-1111-1111-1111-111111111111"
            ),
            distinguished_name=(
                "CN=Alice,OU=Users,DC=example,DC=com"
            ),
            username="alice",
            display_name="Alice Example",
            user_principal_name="alice@example.com",
            mail="alice@example.com",
            role="User",
        ),
        SimpleNamespace(
            object_guid=(
                "22222222-2222-2222-2222-222222222222"
            ),
            distinguished_name=(
                "CN=Admin,OU=Users,DC=example,DC=com"
            ),
            username="admin",
            display_name="Admin Example",
            user_principal_name="admin@example.com",
            mail="admin@example.com",
            role="Administrator",
        ),
    ]

    fake_client = SimpleNamespace(
        role_users=lambda: fake_users,
    )

    monkeypatch.setattr(
        routes,
        "get_directory_client",
        lambda settings: fake_client,
    )

    response = client.get(
        "/users",
        headers=identity_headers("admin"),
    )

    assert response.status_code == 200
    assert b"Alice Example" in response.data
    assert b"Administrator" in response.data


def test_team_is_revalidated_and_stored_by_guid(
    app,
    client,
    monkeypatch,
):
    client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
    )

    group = SimpleNamespace(
        object_guid=(
            "33333333-3333-3333-3333-333333333333"
        ),
        distinguished_name=(
            "CN=Network Operations,OU=Groups,"
            "DC=example,DC=com"
        ),
        sam_account_name="Network Operations",
        display_name="Network Operations",
        description="Network operations team.",
    )

    fake_client = SimpleNamespace(
        find_group_by_dn=lambda dn: group,
    )

    monkeypatch.setattr(
        routes,
        "get_directory_client",
        lambda settings: fake_client,
    )

    response = client.post(
        "/teams",
        data={
            "distinguished_name": (
                group.distinguished_name
            ),
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        team = Team.query.one()
        assert team.object_guid == group.object_guid
        assert team.display_name == (
            "Network Operations"
        )
        assert team.created_by == "admin"


def test_directory_backed_permission_validation_rejects_free_text():
    from app.services.project_package_permissions import (
        validate_package_permission_rows,
    )

    errors, rows = validate_package_permission_rows(
        [
            {
                "principal_key": (
                    "legacy|user|made.up.user"
                ),
            }
        ],
        allowed_principals={},
    )

    assert errors
    assert "eligible Active Directory user" in errors[0]
    assert rows[0]["principal_name"] == ""


def test_directory_backed_permission_uses_canonical_guid():
    from app.services.project_package_permissions import (
        validate_package_permission_rows,
    )

    object_guid = (
        "44444444-4444-4444-4444-444444444444"
    )
    key = "user|{}".format(object_guid)
    canonical = {
        "principal_type": "user",
        "principal_name": "alice",
        "principal_object_guid": object_guid,
        "principal_dn": (
            "CN=Alice,OU=Users,DC=example,DC=com"
        ),
    }

    errors, rows = validate_package_permission_rows(
        [
            {
                "principal_key": key,
            }
        ],
        allowed_principals={
            key: canonical,
        },
    )

    assert errors == []
    assert rows == [canonical]


def test_package_team_permission_is_selected_from_registered_team(
    app,
    client,
    seeded_packages,
    monkeypatch,
):
    import app.services.package_principals as package_principals

    client.post(
        "/settings/directory",
        data=directory_form_data(),
        headers=identity_headers("admin"),
    )

    team_guid = (
        "55555555-5555-5555-5555-555555555555"
    )

    with app.app_context():
        team = Team(
            object_guid=team_guid,
            distinguished_name=(
                "CN=Automation Support,OU=Groups,"
                "DC=example,DC=com"
            ),
            sam_account_name="Automation Support",
            display_name="Automation Support",
            description="",
            created_by="admin",
        )
        db.session.add(team)
        db.session.commit()

    fake_client = SimpleNamespace(
        role_users=lambda: [],
    )

    monkeypatch.setattr(
        package_principals,
        "get_directory_client",
        lambda settings: fake_client,
    )

    response = client.post(
        "/packages/new",
        data={
            "name": "Team Permission Package",
            "description": "",
            "project_id": str(
                seeded_packages[
                    "enabled_project"
                ]
            ),
            "enabled": "on",
            "access_mode": "restricted",
            "warning_message": "",
            "confirmation_required": "on",
            "confirmation_message": "",
            "fixed_vars_yaml": "{}",
            "package_permission_row": ["1"],
            "package_permission_1_principal_key": (
                "group|{}".format(team_guid)
            ),
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
        assert permission.principal_name == (
            "Automation Support"
        )
        assert permission.principal_object_guid == (
            team_guid
        )


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
    from checks import assert_output_equal

    valid_permission = SimpleNamespace(
        principal_object_guid=(
            "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        ),
        package=SimpleNamespace(name="Valid Package"),
    )
    orphaned_permission = SimpleNamespace(
        principal_object_guid=(
            "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"
        ),
        package=None,
    )

    grants = routes._package_grants_by_principal_guid(
        [valid_permission, orphaned_permission]
    )

    assert_output_equal(
        grants,
        {
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": [
                "Valid Package"
            ]
        },
        purpose=(
            "Users and Teams grant summaries ignore a stale Package "
            "permission whose Package has already been deleted"
        ),
    )
