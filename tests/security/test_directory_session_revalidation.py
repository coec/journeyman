"""ASVS evidence for active-session Active Directory revalidation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db


pytestmark = pytest.mark.security


def _expired_directory_check(app, client):
    from app.models import AuthSession
    with client.session_transaction() as browser_session:
        session_id = browser_session["journeyman_session_id"]
    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        row.directory_checked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.session.commit()
    return session_id


def _resolved_identity(username="alice", *, role="User"):
    return SimpleNamespace(
        user=SimpleNamespace(
            username=username,
            display_name="Alice Example",
            object_guid="11111111-1111-1111-1111-111111111111",
        ),
        role=role,
        groups=(
            SimpleNamespace(
                sam_account_name=("Journeyman Admins" if role == "Administrator" else "Journeyman Users"),
                object_guid="22222222-2222-2222-2222-222222222222",
            ),
        ),
    )


def test_disabled_or_deleted_directory_user_revokes_active_session(app, client, monkeypatch):
    from app.services import directory, directory_settings
    from tests.security.test_session_management_controls import _login

    assert _login(app, client, monkeypatch).status_code == 302
    session_id = _expired_directory_check(app, client)

    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class Client:
        def resolve_user_access(self, username):
            raise directory.DirectoryAuthenticationError("disabled or deleted")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: Client())

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    from app.models import AuthSession
    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row.revoked_at is not None


def test_directory_revalidation_refreshes_role_and_group_snapshot(app, client, monkeypatch):
    from app.services import directory, directory_settings
    from tests.security.test_session_management_controls import _login

    assert _login(app, client, monkeypatch).status_code == 302
    _expired_directory_check(app, client)

    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class Client:
        def resolve_user_access(self, username):
            return _resolved_identity(username, role="Administrator")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: Client())

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        identity = browser_session["journeyman_identity"]
        assert identity["role"] == "Administrator"
        assert identity["group_names"] == ["Journeyman Admins"]


def test_directory_outage_denies_request_without_revoking_session(app, client, monkeypatch):
    from app.services import directory, directory_settings
    from tests.security.test_session_management_controls import _login

    assert _login(app, client, monkeypatch).status_code == 302
    session_id = _expired_directory_check(app, client)

    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class Client:
        def resolve_user_access(self, username):
            raise directory.DirectoryUnavailableError("directory unavailable")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: Client())

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    from app.models import AuthSession
    with app.app_context():
        row = db.session.get(AuthSession, session_id)
        assert row.revoked_at is None


def test_directory_revalidation_is_not_performed_inside_interval(app, client, monkeypatch):
    from app.services import directory, directory_settings
    from tests.security.test_session_management_controls import _login

    assert _login(app, client, monkeypatch).status_code == 302
    app.config["AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS"] = 300

    monkeypatch.setattr(directory_settings, "get_or_create_directory_settings", lambda: object())

    class Client:
        def resolve_user_access(self, username):
            raise AssertionError("directory should not be queried yet")

    monkeypatch.setattr(directory, "get_directory_client", lambda _settings: Client())
    assert client.get("/", follow_redirects=False).status_code == 200
