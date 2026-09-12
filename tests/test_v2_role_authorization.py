from flask import g

from app import db
from app.auth import (
    current_user_can_access_automation,
    current_user_can_access_platform,
    current_user_can_access_resources,
    current_user_can_audit,
    current_user_can_manage_automation,
    current_user_can_manage_resources,
    current_user_can_view_automation,
    current_user_can_view_platform,
    current_user_can_view_resources,
    current_user_is_admin,
)
from app.models import AuthorizationRole, UserAccount
from app.services.authorization import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_AUTOMATION_ADMIN,
    ROLE_RESOURCE_ADMIN,
    ROLE_USER,
    sync_legacy_directory_user,
)


def _set_identity(username, *, via="ldap"):
    g.authenticated_username = username
    g.authenticated_role = "User"
    g.authenticated_via = via


def _account(username, *role_names):
    account = UserAccount(username=username, display_name=username, enabled=True)
    db.session.add(account)
    for name in role_names:
        account.roles.append(AuthorizationRole.query.filter_by(name=name).one())
    db.session.commit()
    return account


def test_platform_admin_does_not_implicitly_manage_automation_or_resources(app):
    with app.test_request_context("/"):
        _account("platform", ROLE_ADMIN, ROLE_USER)
        _set_identity("platform")
        assert current_user_is_admin()
        assert not current_user_can_manage_automation()
        assert not current_user_can_manage_resources()
        assert current_user_can_audit()


def test_specialist_admin_roles_are_independent(app):
    with app.test_request_context("/"):
        _account("automation", ROLE_AUTOMATION_ADMIN, ROLE_USER)
        _set_identity("automation")
        assert current_user_can_manage_automation()
        assert not current_user_can_manage_resources()
        assert not current_user_is_admin()

        db.session.rollback()

    with app.test_request_context("/"):
        _set_identity("resource")
        _account("resource", ROLE_RESOURCE_ADMIN, ROLE_USER)
        assert current_user_can_manage_resources()
        assert not current_user_can_manage_automation()
        assert not current_user_is_admin()


def test_auditor_can_read_audit_but_has_no_admin_capability(app):
    with app.test_request_context("/"):
        _account("auditor", ROLE_AUDITOR)
        _set_identity("auditor")
        assert current_user_can_audit()
        assert not current_user_is_admin()
        assert not current_user_can_manage_automation()
        assert not current_user_can_manage_resources()


def test_break_glass_has_all_management_domains(app):
    with app.test_request_context("/"):
        _set_identity("breakglass", via="fallback")
        assert current_user_is_admin()
        assert current_user_can_manage_automation()
        assert current_user_can_manage_resources()
        assert current_user_can_audit()


def test_new_legacy_directory_admin_gets_compatibility_admin_roles(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="legacy.admin",
            directory_object_guid="77777777-7777-7777-7777-777777777777",
            legacy_role="Administrator",
        )
        assert account.has_role(ROLE_ADMIN)
        assert account.has_role(ROLE_AUTOMATION_ADMIN)
        assert account.has_role(ROLE_RESOURCE_ADMIN)
        assert account.has_role(ROLE_USER)


def test_auditor_has_read_only_access_to_all_admin_domains(app):
    with app.test_request_context("/projects", method="GET"):
        _account("auditor.read", ROLE_AUDITOR)
        _set_identity("auditor.read")
        assert current_user_can_view_platform()
        assert current_user_can_view_automation()
        assert current_user_can_view_resources()
        assert current_user_can_access_platform()
        assert current_user_can_access_automation()
        assert current_user_can_access_resources()

    with app.test_request_context("/projects", method="POST"):
        _set_identity("auditor.read")
        assert not current_user_can_access_platform()
        assert not current_user_can_access_automation()
        assert not current_user_can_access_resources()
