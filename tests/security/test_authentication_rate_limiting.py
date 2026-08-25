"""ASVS evidence for login anti-automation controls."""

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.security


def _enable_authentication(app):
    app.config["AUTHENTICATION_DISABLED"] = False
    app.config["LOGIN_RATE_LIMIT_ATTEMPTS"] = 3
    app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = 60


def _reset_rate_limit_state():
    from app import auth
    with auth._LOGIN_FAILURE_LOCK:
        auth._LOGIN_FAILURES.clear()


@pytest.fixture(autouse=True)
def clear_login_rate_limits():
    _reset_rate_limit_state()
    yield
    _reset_rate_limit_state()


def test_repeated_failed_logins_are_rate_limited(app, client, monkeypatch):
    _enable_authentication(app)

    from app.services import directory, directory_settings
    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class RejectingClient:
        def authenticate_user(self, username, password):
            raise RuntimeError("invalid credentials")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: RejectingClient())

    for _ in range(3):
        response = client.post(
            "/login",
            data={"username": "alice", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        data={"username": "alice", "password": "wrong"},
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert b"Too many login attempts" in response.data


def test_source_wide_limit_prevents_username_rotation_bypass(app, client, monkeypatch):
    _enable_authentication(app)

    from app.services import directory, directory_settings
    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class RejectingClient:
        def authenticate_user(self, username, password):
            raise RuntimeError("invalid credentials")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: RejectingClient())

    for username in ("alice", "bob", "charlie"):
        assert client.post(
            "/login",
            data={"username": username, "password": "wrong"},
            environ_base={"REMOTE_ADDR": "192.0.2.11"},
        ).status_code == 401

    assert client.post(
        "/login",
        data={"username": "different-user", "password": "wrong"},
        environ_base={"REMOTE_ADDR": "192.0.2.11"},
    ).status_code == 429


def test_success_clears_account_source_failure_bucket(app, client, monkeypatch):
    _enable_authentication(app)

    from app.services import directory, directory_settings
    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class Client:
        def authenticate_user(self, username, password):
            if password != "correct":
                raise RuntimeError("invalid credentials")
            return SimpleNamespace(
                user=SimpleNamespace(
                    username=username,
                    display_name="Alice",
                    object_guid="11111111-1111-1111-1111-111111111111",
                ),
                role="User",
                groups=(),
            )

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: Client())

    for _ in range(2):
        assert client.post(
            "/login",
            data={"username": "alice", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "192.0.2.12"},
        ).status_code == 401

    assert client.post(
        "/login",
        data={"username": "alice", "password": "correct"},
        environ_base={"REMOTE_ADDR": "192.0.2.12"},
    ).status_code == 302

    from app import auth
    account_key = ("account_source", "alice", "192.0.2.12")
    assert account_key not in auth._LOGIN_FAILURES
