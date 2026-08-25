from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db
from app.models import ApiToken, Credential, SignalSource
from app.services import api_tokens
from app.services.secret_lifecycle import credential_too_old, security_notices_for_identity

pytestmark = pytest.mark.security


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def test_created_api_token_has_hard_twelve_month_expiry(app, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(api_tokens, 'utcnow', lambda: now)
    with app.app_context():
        row, secret = api_tokens.create_api_token(name='automation', username='owner')
        assert _utc(row.expires_at) == now + timedelta(days=365)
        assert api_tokens.authenticate_api_token(secret) is row

        monkeypatch.setattr(api_tokens, 'utcnow', lambda: now + timedelta(days=365))
        assert api_tokens.authenticate_api_token(secret) is None


def test_api_owner_is_warned_from_thirty_days_before_expiry(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        row = ApiToken(
            name='expiring', username='api-owner', role='User', token_digest='a' * 64,
            enabled=True, created_at=now - timedelta(days=335), expires_at=now + timedelta(days=30),
        )
        db.session.add(row)
        db.session.commit()
        notices = security_notices_for_identity('api-owner', now=now)
        assert any('expires on' in notice['message'] for notice in notices)


def test_api_response_warns_consumer_during_final_thirty_days(app, monkeypatch):
    from app.services.api_tokens import _digest
    now = datetime.now(timezone.utc)
    secret = 'jym1_expiring-test-token'
    with app.app_context():
        db.session.add(ApiToken(
            name='expiring-api', username='api-owner', role='User', token_digest=_digest(secret),
            enabled=True, created_at=now - timedelta(days=340), expires_at=now + timedelta(days=25),
        ))
        db.session.commit()
    client = app.test_client()
    response = client.get('/api/v1/projects', headers={'Authorization': 'Bearer ' + secret})
    assert response.status_code == 200
    assert response.headers['X-Journeyman-API-Token-Expiry-Warning'] == 'true'
    assert 'within 30 days' in response.headers['Warning']


def test_external_credentials_are_advisory_only_when_older_than_twelve_months(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        credential = Credential(
            name='remote-system', owner='owner', security_scope='private',
            credential_type='machine', username='remote-user', encrypted_data=b'opaque-test-ciphertext',
            created_at=now - timedelta(days=500), updated_at=now, secret_updated_at=now - timedelta(days=366),
        )
        db.session.add(credential)
        db.session.commit()
        assert credential_too_old(credential, now=now) is True

        credential.description = 'metadata edit does not refresh secret age'
        credential.updated_at = now
        db.session.commit()
        assert credential_too_old(credential, now=now) is True


def test_signal_source_hmac_secret_age_warns_admin_without_disabling_source(app, monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    key = tmp_path / 'session.key'
    meta = tmp_path / 'session.json'
    from journeyman_session_key import prepare_session_signing_key
    prepare_session_signing_key(key_path=key, metadata_path=meta, now=now, uptime_seconds=600, boot_id='test', legacy_secret='safe-session-secret')
    app.config['SESSION_SIGNING_KEY_FILE'] = str(key)
    app.config['SESSION_SIGNING_KEY_METADATA_FILE'] = str(meta)

    with app.app_context():
        source = SignalSource(
            name='Monitoring', source_type='zabbix', enabled=True,
            secret_created_at=now - timedelta(days=340),
        )
        db.session.add(source)
        db.session.commit()
        notices = security_notices_for_identity('admin', is_admin=True, now=now)
        assert any('HMAC secret' in notice['message'] and 'due within 30 days' in notice['message'] for notice in notices)
        assert source.enabled is True
