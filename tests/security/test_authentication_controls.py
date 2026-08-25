"""Cross-cutting authentication controls used as ASVS evidence."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash


pytestmark = pytest.mark.security


def _enable_authentication(app):
    app.config["AUTHENTICATION_DISABLED"] = False


def _successful_directory_identity(username="alice"):
    return SimpleNamespace(
        user=SimpleNamespace(
            username=username,
            display_name="Alice Example",
            object_guid="11111111-1111-1111-1111-111111111111",
        ),
        role="User",
        groups=(),
    )


def _stub_directory_authentication(monkeypatch, captured):
    from app.services import directory, directory_settings

    monkeypatch.setattr(
        directory_settings,
        "get_or_create_directory_settings",
        lambda: object(),
    )

    class StubDirectoryClient:
        def authenticate_user(self, username, password):
            captured["username"] = username
            captured["password"] = password
            return _successful_directory_identity(username)

    monkeypatch.setattr(
        directory,
        "get_directory_client",
        lambda _settings: StubDirectoryClient(),
    )


def test_login_password_field_is_masked_and_does_not_disable_paste(app, client):
    _enable_authentication(app)

    response = client.get("/login")

    assert response.status_code == 200
    assert b'name="password" type="password"' in response.data
    assert b'autocomplete="current-password"' in response.data
    assert b"onpaste=" not in response.data.lower()


def test_login_passes_password_to_directory_exactly_as_received(
    app,
    client,
    monkeypatch,
):
    _enable_authentication(app)
    captured = {}
    _stub_directory_authentication(monkeypatch, captured)
    password = "  Mixed CASE ; $pecial {{ value }}  "

    response = client.post(
        "/login",
        data={"username": "alice", "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured == {
        "username": "alice",
        "password": password,
    }


def test_login_allows_password_longer_than_64_characters(
    app,
    client,
    monkeypatch,
):
    _enable_authentication(app)
    captured = {}
    _stub_directory_authentication(monkeypatch, captured)
    password = "A" * 128

    response = client.post(
        "/login",
        data={"username": "alice", "password": password},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured["password"] == password


def test_fallback_administrator_is_unusable_until_hash_is_provisioned(app, client):
    _enable_authentication(app)
    hash_path = Path(app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"])
    hash_path.unlink(missing_ok=True)

    response = client.post(
        "/login",
        data={"username": app.config["FALLBACK_ADMIN_USERNAME"], "password": "anything"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert b"Invalid username or password." in response.data
    with client.session_transaction() as session:
        assert "journeyman_identity" not in session


def test_fallback_administrator_rejects_unsafe_hash_file_permissions(app, client):
    _enable_authentication(app)
    hash_path = Path(app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"])
    hash_path.write_text(
        generate_password_hash("correct horse battery staple", method="scrypt") + "\n",
        encoding="utf-8",
    )
    hash_path.chmod(0o666)

    from app.services.fallback_admin import provision_fallback_activation
    with app.app_context():
        provision_fallback_activation()

    response = client.post(
        "/login",
        data={
            "username": app.config["FALLBACK_ADMIN_USERNAME"],
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    with client.session_transaction() as session:
        assert "journeyman_identity" not in session


def test_login_page_has_no_password_hint_or_knowledge_question_fields(app, client):
    _enable_authentication(app)

    response = client.get("/login")
    body = response.data.lower()

    assert response.status_code == 200
    assert b"password hint" not in body
    assert b"security question" not in body
    assert b"secret question" not in body
