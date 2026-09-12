from app import db
from app.auth import can_launch_package
from app.models import (
    AuthorizationRole,
    Project,
    ProjectPackage,
    ProjectPackagePermission,
    Team,
    UserAccount,
)
from app.services.authorization import ROLE_AUDITOR, ROLE_USER
from app.services.package_principals import package_principal_context


def _account(username, role_name):
    role = AuthorizationRole.query.filter_by(name=role_name).one()
    account = UserAccount(
        username=username,
        display_name=username.title(),
        enabled=True,
    )
    db.session.add(account)
    account.roles.append(role)
    return account


def _restricted_package(name="Local Team Package"):
    project = Project(
        name=name + " Project",
        description="",
        enabled=True,
        owner="test",
        security_scope="private",
    )
    package = ProjectPackage(
        name=name,
        description="",
        project=project,
        enabled=True,
        owner="test",
        access_mode="restricted",
    )
    db.session.add_all([project, package])
    return package


def test_local_team_membership_grants_restricted_package(app):
    with app.app_context():
        alice = _account("alice", ROLE_USER)
        bob = _account("bob", ROLE_USER)
        team = Team.new_local(
            display_name="Oracle DBAs",
            description="Database administrators",
            created_by="admin",
        )
        team.members.append(alice)
        package = _restricted_package()
        db.session.add(team)
        db.session.flush()
        package.permissions.append(
            ProjectPackagePermission(
                principal_type="group",
                principal_name=team.display_name,
                principal_object_guid=team.object_guid,
                principal_dn=team.distinguished_name,
                team_id=team.id,
            )
        )
        db.session.commit()

        assert can_launch_package(package, username="alice", is_admin=False)
        assert not can_launch_package(
            package,
            username="bob",
            group_names={"Oracle DBAs"},
            is_admin=False,
        )


def test_direct_local_user_grant_requires_user_role(app):
    with app.app_context():
        alice = _account("alice.direct", ROLE_USER)
        auditor = _account("audit.only", ROLE_AUDITOR)
        package = _restricted_package("Direct User Package")
        db.session.flush()
        package.permissions.extend(
            [
                ProjectPackagePermission(
                    principal_type="user",
                    principal_name=alice.username,
                    user_account_id=alice.id,
                ),
                ProjectPackagePermission(
                    principal_type="user",
                    principal_name=auditor.username,
                    user_account_id=auditor.id,
                ),
            ]
        )
        db.session.commit()

        assert can_launch_package(package, username="alice.direct", is_admin=False)
        assert not can_launch_package(package, username="audit.only", is_admin=False)


def test_package_principal_context_uses_local_users_and_teams(app):
    with app.app_context():
        alice = _account("alice.context", ROLE_USER)
        _account("auditor.context", ROLE_AUDITOR)
        team = Team.new_local(
            display_name="Control System Engineers",
            description="",
            created_by="admin",
        )
        team.members.append(alice)
        db.session.add(team)
        db.session.commit()

        context = package_principal_context()
        keys = {choice["key"] for choice in context["choices"]}

        assert "user|{}".format(alice.id) in keys
        assert "team|{}".format(team.id) in keys
        assert all("auditor.context" not in choice["label"] for choice in context["choices"])
        assert context["error"] == ""
