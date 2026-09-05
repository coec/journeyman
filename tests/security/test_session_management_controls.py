"""Cross-cutting session-management controls used as ASVS evidence."""

from http.cookies import SimpleCookie
from types import SimpleNamespace

import pytest

from app import db


pytestmark = pytest.mark.security


def _enable_authentication(app):
    app.config["AUTHENTICATION_DISABLED"] = False


def _stub_directory_authentication(monkeypatch, username="alice"):
    from app.services import directory, directory_settings

    monkeypatch.setattr(
        directory_settings,
        "get_or_create_directory_settings",
        lambda: object(),
    )

    class StubDirectoryClient:
        def authenticate_user(self, submitted_username, _password):
            return SimpleNamespace(
                user=SimpleNamespace(
                    username=submitted_username,
                    display_name="Alice Example",
                    object_guid="11111111-1111-1111-1111-111111111111",
                ),
                role="User",
                groups=(),
            )

    monkeypatch.setattr(
        directory,
        "get_directory_client",
        lambda _settings: StubDirectoryClient(),
    )


def _session_cookie_from_response(app, response):
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    parsed = SimpleCookie()
    for header in response.headers.getlist("Set-Cookie"):
        parsed.load(header)
    morsel = parsed.get(cookie_name)
    assert morsel is not None
    return morsel.value


def _login(app, client, monkeypatch, username="alice"):
    _enable_authentication(app)
    _stub_directory_authentication(monkeypatch, username=username)
    return client.post(
        "/login",
        data={"username": username, "password": "correct horse battery staple"},
        follow_redirects=False,
    )


def test_tampered_session_cookie_does_not_authenticate(app, client, monkeypatch):
    response = _login(app, client, monkeypatch)
    assert response.status_code == 302
    original = _session_cookie_from_response(app, response)

    # Flask/itsdangerous uses URL-safe Base64 for the cookie signature.
    # Mutating the final Base64 character can change only unused padding
    # bits and therefore decode to the same signature bytes. Mutate the
    # first character of the signature instead so the signed value is
    # unambiguously different.
    signed_value, signature = original.rsplit(".", 1)
    replacement = "A" if signature[:1] != "A" else "B"
    tampered = "{}.{}{}".format(
        signed_value,
        replacement,
        signature[1:],
    )

    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    anonymous_client = app.test_client(use_cookies=False)
    response = anonymous_client.get(
        "/",
        headers={"Cookie": "{}={}".format(cookie_name, tampered)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_replaces_pre_authentication_session_cookie(app, client, monkeypatch):
    _enable_authentication(app)
    _stub_directory_authentication(monkeypatch)

    with client.session_transaction() as browser_session:
        browser_session["pre_auth_marker"] = "attacker-known-state"

    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    before = client.get_cookie(cookie_name)
    assert before is not None

    response = client.post(
        "/login",
        data={"username": "alice", "password": "correct horse battery staple"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    after = client.get_cookie(cookie_name)
    assert after is not None
    assert after.value != before.value
    with client.session_transaction() as browser_session:
        assert "pre_auth_marker" not in browser_session
        assert browser_session["journeyman_identity"]["username"] == "alice"


def test_authenticated_session_cookie_supports_maximum_idle_timeout(
    app,
    client,
    monkeypatch,
):
    response = _login(app, client, monkeypatch)
    assert response.status_code == 302

    lifetime = app.permanent_session_lifetime
    assert int(lifetime.total_seconds()) == 604800
    assert app.config.get("SESSION_REFRESH_EACH_REQUEST", True) is True
    with client.session_transaction() as browser_session:
        assert browser_session.permanent is True

    set_cookie = "\n".join(response.headers.getlist("Set-Cookie"))
    assert "Expires=" in set_cookie


def test_logout_clears_browser_session_and_requires_login_again(app, client, monkeypatch):
    response = _login(app, client, monkeypatch)
    assert response.status_code == 302

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    with client.session_transaction() as browser_session:
        assert "journeyman_identity" not in browser_session

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_authenticated_pages_expose_visible_logout_control(app, client, monkeypatch):
    _login(app, client, monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert b'action="/logout"' in response.data
    assert b"Sign out" in response.data


def test_session_creation_requires_explicit_successful_login(app, client, monkeypatch):
    _enable_authentication(app)

    response = client.get("/login")
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        assert "journeyman_identity" not in browser_session

    _stub_directory_authentication(monkeypatch)
    response = client.post(
        "/login",
        data={"username": "alice", "password": "correct horse battery staple"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert browser_session["journeyman_identity"]["username"] == "alice"


def test_production_rejects_default_session_signing_secret(tmp_path, monkeypatch):
    from app import create_app

    repository_root = tmp_path / "repositories"
    log_root = tmp_path / "logs"
    repository_root.mkdir()
    log_root.mkdir()

    config = type(
        "UnsafeProductionConfig",
        (),
        {
            "DEBUG": False,
            "TESTING": True,
            "SECRET_KEY": "development-only-change-me",
            "AUTHENTICATION_DISABLED": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "REPOSITORY_ROOT": repository_root,
            "LOG_ROOT": log_root,
            "PROXY_FIX_X_FOR": 0,
            "PROXY_FIX_X_PROTO": 0,
            "PROXY_FIX_X_HOST": 0,
            "PROXY_FIX_X_PORT": 0,
        },
    )

    with pytest.raises(RuntimeError, match="managed session-signing key"):
        create_app(config, instance_path=tmp_path / "instance")


def test_logout_revokes_copied_session_cookie_server_side(app, client, monkeypatch):
    response = _login(app, client, monkeypatch)
    assert response.status_code == 302
    copied_cookie = _session_cookie_from_response(app, response)

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 302

    from app.models import AuthSession
    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row is not None
        assert row.revoked_at is not None

    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    copied_client = app.test_client(use_cookies=False)
    response = copied_client.get(
        "/",
        headers={"Cookie": "{}={}".format(cookie_name, copied_cookie)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_absolute_session_lifetime_is_enforced_server_side(app, client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import AuthSession

    response = _login(app, client, monkeypatch)
    assert response.status_code == 302

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row.revoked_at is not None


def test_per_user_idle_session_timeout_is_enforced_server_side(app, client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import AuthSession, UserPreference

    response = _login(app, client, monkeypatch)
    assert response.status_code == 302

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    with app.app_context():
        db.session.add(UserPreference(username="alice", idle_session_timeout_minutes=60))
        row = db.session.get(AuthSession, session_id)
        row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=61)
        row.directory_checked_at = datetime.now(timezone.utc)
        db.session.commit()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row.revoked_at is not None


def test_active_session_with_two_day_idle_preference_remains_valid(app, client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import AuthSession, UserPreference

    response = _login(app, client, monkeypatch)
    assert response.status_code == 302

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    with app.app_context():
        db.session.add(UserPreference(username="alice", idle_session_timeout_minutes=2880))
        row = db.session.get(AuthSession, session_id)
        row.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=47)
        row.directory_checked_at = datetime.now(timezone.utc)
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        db.session.commit()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
