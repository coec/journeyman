from app import db
from app.models import AuthorizationRight, AuthorizationRole, AuditLog, UserAccount
from app.services.authorization import ROLE_ADMIN, ROLE_AUDITOR, ROLE_USER, RIGHT_APPROVER, RIGHT_REVIEWER


def identity_headers(username):
    return {"X-Test-Username": username}


def test_admin_users_page_works_without_directory(client):
    response = client.get("/users", headers=identity_headers("admin"))

    assert response.status_code == 200
    assert b"Journeyman-local roles and rights" in response.data
    assert b"No Journeyman users yet" in response.data
    assert b"break-glass administrator is intentionally not represented" in response.data


def test_admin_can_preprovision_user_with_multiple_roles_and_rights(app, client):
    response = client.post(
        "/users/new",
        data={
            "username": "alice",
            "display_name": "Alice Example",
            "enabled": "1",
            "roles": [ROLE_USER, ROLE_AUDITOR],
            "rights": [RIGHT_REVIEWER, RIGHT_APPROVER],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        account = UserAccount.query.filter_by(username="alice").one()
        assert account.enabled is True
        assert {role.name for role in account.roles} == {ROLE_USER, ROLE_AUDITOR}
        assert {right.name for right in account.rights} == {RIGHT_REVIEWER, RIGHT_APPROVER}
        assert AuditLog.query.filter_by(action="user.authorization.create").count() == 1


def test_admin_can_change_local_authorization_without_changing_username(app, client):
    with app.app_context():
        user_role = AuthorizationRole.query.filter_by(name=ROLE_USER).one()
        account = UserAccount(username="bob", display_name="Bob", enabled=True)
        account.roles.append(user_role)
        db.session.add(account)
        db.session.commit()
        user_id = account.id

    response = client.post(
        f"/users/{user_id}/edit",
        data={
            "display_name": "Bob Reviewer",
            "enabled": "1",
            "roles": [ROLE_USER, ROLE_ADMIN],
            "rights": [RIGHT_REVIEWER],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        account = db.session.get(UserAccount, user_id)
        assert account.username == "bob"
        assert account.display_name == "Bob Reviewer"
        assert {role.name for role in account.roles} == {ROLE_USER, ROLE_ADMIN}
        assert {right.name for right in account.rights} == {RIGHT_REVIEWER}
        assert AuditLog.query.filter_by(action="user.authorization.update").count() == 1


def test_admin_can_disable_local_user(app, client):
    with app.app_context():
        account = UserAccount(username="disabled.test", display_name="Disabled Test", enabled=True)
        db.session.add(account)
        db.session.commit()
        user_id = account.id

    response = client.post(
        f"/users/{user_id}/edit",
        data={
            "display_name": "Disabled Test",
            "roles": [],
            "rights": [],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        account = db.session.get(UserAccount, user_id)
        assert account.enabled is False


def test_non_admin_cannot_administer_local_users(client):
    response = client.get("/users/new", headers=identity_headers("alice"))
    assert response.status_code == 403

    response = client.post(
        "/users/new",
        data={"username": "mallory"},
        headers=identity_headers("alice"),
    )
    assert response.status_code == 403


def test_admin_role_form_submission_persists_user_implication(app, client):
    response = client.post(
        "/users/new",
        data={
            "username": "admin.only.submitted",
            "display_name": "Admin Only Submitted",
            "enabled": "1",
            "roles": [ROLE_ADMIN],
            "rights": [],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        account = UserAccount.query.filter_by(username="admin.only.submitted").one()
        assert {role.name for role in account.roles} == {ROLE_ADMIN, ROLE_USER}


def test_auditor_form_submission_does_not_gain_user(app, client):
    response = client.post(
        "/users/new",
        data={
            "username": "auditor.only",
            "display_name": "Auditor Only",
            "enabled": "1",
            "roles": [ROLE_AUDITOR],
            "rights": [],
        },
        headers=identity_headers("admin"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        account = UserAccount.query.filter_by(username="auditor.only").one()
        assert {role.name for role in account.roles} == {ROLE_AUDITOR}
