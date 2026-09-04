"""Security tests for the temporary break-glass administrator lifecycle."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import AuthSession, FallbackAdminActivation
from app.services import fallback_admin


pytestmark = pytest.mark.security
PASSWORD = "correct horse battery staple"


def _enable_authentication(app):
    app.config["AUTHENTICATION_DISABLED"] = False


def _provision(app, *, now):
    hash_path = Path(app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"])
    hash_path.write_text(
        generate_password_hash(PASSWORD, method="scrypt") + "\n",
        encoding="utf-8",
    )
    hash_path.chmod(0o640)
    with app.app_context():
        activation = fallback_admin.provision_fallback_activation(now=now)
        return (
            activation.activated_at.replace(tzinfo=timezone.utc),
            activation.expires_at.replace(tzinfo=timezone.utc),
        )


def _login(client, app):
    return client.post(
        "/login",
        data={
            "username": app.config["FALLBACK_ADMIN_USERNAME"],
            "password": PASSWORD,
        },
        follow_redirects=False,
    )


def test_fallback_activation_has_fixed_sixty_minute_lifetime(app):
    now = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
    with app.app_context():
        activation = fallback_admin.provision_fallback_activation(now=now)
        assert activation.activated_at.replace(tzinfo=timezone.utc) == now
        assert activation.expires_at.replace(tzinfo=timezone.utc) == now + timedelta(minutes=60)
        assert activation.expired_at is None


def test_fallback_login_requires_current_activation(app, client):
    _enable_authentication(app)
    hash_path = Path(app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"])
    hash_path.write_text(generate_password_hash(PASSWORD, method="scrypt") + "\n", encoding="utf-8")
    hash_path.chmod(0o640)

    response = _login(client, app)
    assert response.status_code == 401
    with client.session_transaction() as browser_session:
        assert "journeyman_identity" not in browser_session


def test_fallback_session_never_outlives_activation(app, client, monkeypatch):
    _enable_authentication(app)
    now = datetime.now(timezone.utc)
    _, activation_expires_at = _provision(app, now=now)

    monkeypatch.setattr("app.auth._utc_now", lambda: now)
    assert _login(client, app).status_code == 302

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row.expires_at.replace(tzinfo=timezone.utc) == activation_expires_at


def test_fallback_timeout_revokes_sessions_and_prevents_relogin(app, client, monkeypatch):
    _enable_authentication(app)
    started = datetime.now(timezone.utc)
    _provision(app, now=started)

    monkeypatch.setattr("app.auth._utc_now", lambda: started)
    assert _login(client, app).status_code == 302

    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]

    expired = started + timedelta(minutes=60)
    monkeypatch.setattr("app.auth._utc_now", lambda: expired)

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with app.app_context():
        activation = db.session.get(FallbackAdminActivation, 1)
        row = db.session.get(AuthSession, session_id)
        assert activation.expired_at is not None
        assert activation.expiry_reason == "timeout"
        assert row.revoked_at is not None

    assert _login(client, app).status_code == 401


def test_fallback_logout_terminates_activation_and_all_fallback_sessions(app, monkeypatch):
    _enable_authentication(app)
    now = datetime.now(timezone.utc)
    _provision(app, now=now)
    monkeypatch.setattr("app.auth._utc_now", lambda: now)

    first = app.test_client()
    second = app.test_client()
    assert _login(first, app).status_code == 302
    assert _login(second, app).status_code == 302

    assert first.post("/logout", follow_redirects=False).status_code == 302

    with app.app_context():
        activation = db.session.get(FallbackAdminActivation, 1)
        active_sessions = (
            AuthSession.query
            .filter(AuthSession.username == app.config["FALLBACK_ADMIN_USERNAME"])
            .filter(AuthSession.revoked_at.is_(None))
            .count()
        )
        assert activation.expired_at is not None
        assert activation.expiry_reason == "logout"
        assert active_sessions == 0

    assert _login(second, app).status_code == 401


def test_reprovision_is_new_activation_not_extension(app):
    first = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
    second = first + timedelta(minutes=20)

    with app.app_context():
        initial = fallback_admin.provision_fallback_activation(now=first)
        initial_deadline = initial.expires_at.replace(tzinfo=timezone.utc)
        replacement = fallback_admin.provision_fallback_activation(now=second)

        assert initial_deadline == first + timedelta(minutes=60)
        assert replacement.activated_at.replace(tzinfo=timezone.utc) == second
        assert replacement.expires_at.replace(tzinfo=timezone.utc) == second + timedelta(minutes=60)


def test_break_glass_ui_exposes_countdown_and_four_warning_thresholds(app, client, monkeypatch):
    _enable_authentication(app)
    now = datetime.now(timezone.utc)
    _provision(app, now=now)
    monkeypatch.setattr("app.auth._utc_now", lambda: now)
    assert _login(client, app).status_code == 302

    response = client.get("/")
    assert response.status_code == 200
    assert b'data-break-glass="true"' in response.data
    assert b"data-break-glass-countdown" in response.data
    assert b"Break-glass administrator" in response.data

    javascript = (Path(app.root_path) / "static" / "js" / "journeyman.js").read_text(encoding="utf-8")
    assert "const warningFractions = [0.5, 0.75, 5 / 6, 11 / 12];" in javascript
    assert "const warningThresholds = warningFractions.map" in javascript
    assert 'window.location.assign("/login")' in javascript


def test_scheduler_side_expiry_is_idempotent(app):
    started = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
    with app.app_context():
        fallback_admin.provision_fallback_activation(now=started)
        assert fallback_admin.expire_fallback_activation_if_due(now=started + timedelta(minutes=59, seconds=59)) is False
        assert fallback_admin.expire_fallback_activation_if_due(now=started + timedelta(minutes=60)) is True
        assert fallback_admin.expire_fallback_activation_if_due(now=started + timedelta(minutes=61)) is False
