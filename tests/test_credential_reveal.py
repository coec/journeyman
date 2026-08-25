from app import db
from app.models import AuditLog, Credential
from app.credential_types import CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES


def _credential(owner="credential.owner"):
    credential = Credential(
        name="Environment login",
        description="Reveal test",
        owner=owner,
        security_scope="private",
        credential_type=CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
        username="svc-example",
    )
    credential.set_credential_data(
        {
            "password": "JM-reveal-canary-secret",
            "username_environment_variable": "EXAMPLE_USERNAME",
            "secret_environment_variable": "EXAMPLE_PASSWORD",
        }
    )
    db.session.add(credential)
    db.session.commit()
    return credential


def test_credential_owner_can_reveal_secret(app, client):
    with app.app_context():
        credential_id = _credential().id

    response = client.post(
        f"/credentials/{credential_id}/reveal",
        headers={"X-Test-Username": "credential.owner"},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.get_json()["values"] == [
        {
            "key": "password",
            "label": "Secret",
            "value": "JM-reveal-canary-secret",
        }
    ]

    with app.app_context():
        event = AuditLog.query.filter_by(
            action="credential.secret_reveal",
            object_id=str(credential_id),
        ).one()
        assert event.actor_username == "credential.owner"
        assert event.result == "success"
        assert "JM-reveal-canary-secret" not in event.details_json


def test_non_owner_admin_cannot_reveal_secret(app, client):
    with app.app_context():
        credential_id = _credential().id

    response = client.post(
        f"/credentials/{credential_id}/reveal",
        headers={"X-Test-Username": "acal002"},
    )

    assert response.status_code == 403

    with app.app_context():
        event = AuditLog.query.filter_by(
            action="credential.secret_reveal",
            object_id=str(credential_id),
        ).one()
        assert event.actor_username == "acal002"
        assert event.result == "denied"


def test_reveal_button_is_visible_only_to_owner(app, client):
    with app.app_context():
        credential_id = _credential().id

    owner_response = client.get(
        f"/credentials?selected={credential_id}",
        headers={"X-Test-Username": "credential.owner"},
    )
    admin_response = client.get(
        f"/credentials?selected={credential_id}",
        headers={"X-Test-Username": "acal002"},
    )

    assert b"Reveal stored secret" in owner_response.data
    assert b"Reveal stored secret" not in admin_response.data
