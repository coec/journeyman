from app import db
from app.models import Project, UserPreference
def test_preferences_default_to_show_disabled(client, app):
    with app.app_context():
        db.session.add(Project(name="Disabled preference project", enabled=False)); db.session.commit()
    response=client.get("/projects",headers={"X-Test-Username":"admin"})
    assert response.status_code==200 and b"Disabled preference project" in response.data
    assert b"Disabled projects hidden" not in response.data
def test_user_can_hide_disabled_projects_and_packages(client, app):
    h={"X-Test-Username":"admin"}
    assert client.post("/preferences",data={"hide_disabled_projects":"1","hide_disabled_packages":"1"},headers=h).status_code==302
    with app.app_context():
        p=UserPreference.query.filter_by(username="admin").one()
        assert p.hide_disabled_projects and p.hide_disabled_packages
    assert b"Disabled projects hidden" in client.get("/projects",headers=h).data
    assert b"Disabled packages hidden" in client.get("/packages",headers=h).data

def test_user_can_choose_rows_per_page(client, app):
    headers = {"X-Test-Username": "admin"}
    response = client.post(
        "/preferences",
        data={"rows_per_page": "100"},
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="admin").one()
        assert preference.rows_per_page == 100


def test_invalid_rows_per_page_falls_back_to_default(client, app):
    headers = {"X-Test-Username": "admin"}
    response = client.post(
        "/preferences",
        data={"rows_per_page": "9999"},
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="admin").one()
        assert preference.rows_per_page == 50


def test_user_can_create_api_token_from_preferences(client, app):
    from app.models import ApiToken
    from app.services.api_tokens import authenticate_api_token

    response = client.post(
        "/preferences/api-tokens",
        data={"name": "alice-script"},
        headers={"X-Test-Username": "alice"},
    )

    assert response.status_code == 201
    body = response.get_data(as_text=True)
    assert "API token created: alice-script" in body
    marker = "jym1_"
    assert marker in body
    secret = marker + body.split(marker, 1)[1].split("<", 1)[0].strip()

    with app.app_context():
        row = ApiToken.query.filter_by(name="alice-script").one()
        assert row.username == "alice"
        assert row.role == "User"
        assert row.token_digest != secret
        assert authenticate_api_token(secret).id == row.id

    subsequent = client.get(
        "/preferences",
        headers={"X-Test-Username": "alice"},
    )
    assert subsequent.status_code == 200
    assert secret.encode() not in subsequent.data
    assert b"alice-script" in subsequent.data


def test_admin_created_api_token_inherits_administrator_role(client, app):
    from app.models import ApiToken

    response = client.post(
        "/preferences/api-tokens",
        data={"name": "admin-script"},
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 201
    with app.app_context():
        row = ApiToken.query.filter_by(name="admin-script").one()
        assert row.username == "admin"
        assert row.role == "Administrator"


def test_preferences_only_list_tokens_owned_by_current_user(client, app):
    from app.models import ApiToken
    from app.services.api_tokens import _digest, utcnow

    with app.app_context():
        now = utcnow()
        db.session.add_all([
            ApiToken(
                name="alice-only", username="alice", role="User",
                token_digest=_digest("jym1_alice"), created_at=now,
            ),
            ApiToken(
                name="bob-only", username="bob", role="User",
                token_digest=_digest("jym1_bob"), created_at=now,
            ),
        ])
        db.session.commit()

    response = client.get(
        "/preferences",
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 200
    assert b"alice-only" in response.data
    assert b"bob-only" not in response.data


def test_user_can_revoke_own_api_token_but_not_another_users(client, app):
    from app.models import ApiToken
    from app.services.api_tokens import _digest, utcnow

    with app.app_context():
        now = utcnow()
        alice = ApiToken(
            name="alice-revoke", username="alice", role="User",
            token_digest=_digest("jym1_alice-revoke"), created_at=now,
        )
        bob = ApiToken(
            name="bob-revoke", username="bob", role="User",
            token_digest=_digest("jym1_bob-revoke"), created_at=now,
        )
        db.session.add_all([alice, bob])
        db.session.commit()
        alice_id = alice.id
        bob_id = bob.id

    response = client.post(
        f"/preferences/api-tokens/{alice_id}/revoke",
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 302

    forbidden = client.post(
        f"/preferences/api-tokens/{bob_id}/revoke",
        headers={"X-Test-Username": "alice"},
    )
    assert forbidden.status_code == 404

    with app.app_context():
        assert db.session.get(ApiToken, alice_id).enabled is False
        assert db.session.get(ApiToken, bob_id).enabled is True


def test_api_token_name_validation_is_shown_in_preferences(client):
    response = client.post(
        "/preferences/api-tokens",
        data={"name": ""},
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 400
    assert b"API token name and username are required" in response.data


def test_user_can_set_idle_session_timeout_in_minutes(client, app):
    headers = {"X-Test-Username": "alice"}
    response = client.post(
        "/preferences",
        data={
            "rows_per_page": "50",
            "idle_session_timeout_value": "60",
            "idle_session_timeout_unit": "minutes",
        },
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="alice").one()
        assert preference.idle_session_timeout_minutes == 60


def test_user_can_set_idle_session_timeout_in_days(client, app):
    headers = {"X-Test-Username": "alice"}
    response = client.post(
        "/preferences",
        data={
            "rows_per_page": "50",
            "idle_session_timeout_value": "2",
            "idle_session_timeout_unit": "days",
        },
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="alice").one()
        assert preference.idle_session_timeout_minutes == 2880


def test_idle_session_timeout_rejects_values_outside_allowed_range(client, app):
    response = client.post(
        "/preferences",
        data={
            "rows_per_page": "50",
            "idle_session_timeout_value": "8",
            "idle_session_timeout_unit": "days",
        },
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 400
    assert b"between 15 minutes and 7 days" in response.data
    with app.app_context():
        preference = UserPreference.query.filter_by(username="alice").one()
        assert preference.idle_session_timeout_minutes == 480
