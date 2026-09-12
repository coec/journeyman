from flask import g

from app import db
from app.auth import (
    current_user_can_develop_project,
    current_user_can_run_project,
    current_user_can_view_project,
)
from app.models import AuthorizationRole, Project, Team, UserAccount
from app.services.authorization import (
    ROLE_AUDITOR,
    ROLE_AUTOMATION_ADMIN,
    ROLE_USER,
)
from app.services.project_development_access import user_can_develop_project


def _account(username, *roles):
    account = UserAccount(
        username=username,
        display_name=username,
        enabled=True,
    )
    db.session.add(account)
    for role_name in roles:
        account.roles.append(
            AuthorizationRole.query.filter_by(name=role_name).one()
        )
    return account


def _project(name="Development ACL Project"):
    project = Project(
        name=name,
        description="",
        enabled=True,
        owner="automation.admin",
        security_scope="private",
    )
    db.session.add(project)
    return project


def _identity(username):
    g.authenticated_username = username
    g.authenticated_role = "User"
    g.authenticated_via = "ldap"


def test_direct_project_development_grant(app):
    with app.app_context():
        alice = _account("alice.dev", ROLE_USER)
        bob = _account("bob.dev", ROLE_USER)
        project = _project()
        project.development_users.append(alice)
        db.session.commit()

        assert user_can_develop_project(project, "alice.dev")
        assert not user_can_develop_project(project, "bob.dev")


def test_team_project_development_grant(app):
    with app.app_context():
        alice = _account("alice.team", ROLE_USER)
        bob = _account("bob.team", ROLE_USER)
        team = Team.new_local(
            display_name="Project Testers",
            description="",
            created_by="automation.admin",
        )
        team.members.append(alice)
        project = _project("Team Development ACL Project")
        project.development_teams.append(team)
        db.session.add(team)
        db.session.commit()

        assert user_can_develop_project(project, "alice.team")
        assert not user_can_develop_project(project, "bob.team")


def test_disabled_account_loses_project_development_access(app):
    with app.app_context():
        alice = _account("alice.disabled", ROLE_USER)
        project = _project("Disabled Development ACL Project")
        project.development_users.append(alice)
        db.session.commit()

        alice.enabled = False
        db.session.commit()

        assert not user_can_develop_project(project, "alice.disabled")


def test_auditor_remains_read_only_even_if_granted(app):
    with app.test_request_context("/projects"):
        auditor = _account("audit.dev", ROLE_AUDITOR)
        project = _project("Auditor Development ACL Project")
        project.development_users.append(auditor)
        db.session.commit()
        _identity("audit.dev")

        assert current_user_can_view_project(project)
        assert not current_user_can_develop_project(project)
        assert not current_user_can_run_project(project)


def test_automation_admin_can_develop_any_project(app):
    with app.test_request_context("/projects"):
        _account("automation.admin", ROLE_AUTOMATION_ADMIN, ROLE_USER)
        project = _project("Automation Admin Project")
        db.session.commit()
        _identity("automation.admin")

        assert current_user_can_view_project(project)
        assert current_user_can_develop_project(project)
        assert current_user_can_run_project(project)

