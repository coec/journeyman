"""Security-secret lifecycle status and user/admin warning generation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from app.models import ApiToken, Credential, SignalSource
from app.credential_crypto import (
    active_credential_key_id,
    credential_key_file,
    credential_keyring_dir,
)
from app.services.api_tokens import token_expiry_warning
from journeyman_session_key import session_signing_key_status

ROTATION_AGE = timedelta(days=365)
ROTATION_WARNING = timedelta(days=30)


def utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_state(timestamp, *, now=None):
    now = _as_utc(now or utcnow())
    timestamp = _as_utc(timestamp)
    if timestamp is None:
        return 'unknown', None
    age = now - timestamp
    if age >= ROTATION_AGE:
        return 'overdue', max(0, int(age.total_seconds() // 86400))
    if age >= ROTATION_AGE - ROTATION_WARNING:
        return 'due_soon', max(0, int(age.total_seconds() // 86400))
    return 'healthy', max(0, int(age.total_seconds() // 86400))


def credential_too_old(credential, *, now=None):

    if credential.encrypted_data is None:
        return False
    state, _ = _age_state(credential.secret_updated_at, now=now)
    return state == 'overdue'


def _active_credential_key_timestamp():
    key_id = active_credential_key_id()
    path = Path(credential_keyring_dir()) / (key_id + '.key') if key_id else Path(credential_key_file())
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc), str(path)
    except OSError:
        return None, str(path)


def security_notices_for_identity(username, *, is_admin=False, now=None):
    now = _as_utc(now or utcnow())
    notices = []
    tokens = ApiToken.query.filter_by(username=username, enabled=True).order_by(ApiToken.expires_at.asc()).all()
    for token in tokens:
        warning = token_expiry_warning(token, now=now)
        if warning is not None:
            notices.append({
                'severity': 'warning',
                'message': 'API token "{}" expires on {}. Replace it before expiry.'.format(
                    token.name, warning['expires_at'].date().isoformat()
                ),
            })

    if not is_admin:
        return notices

    signing = session_signing_key_status(
        key_path=current_app.config.get('SESSION_SIGNING_KEY_FILE'),
        metadata_path=current_app.config.get('SESSION_SIGNING_KEY_METADATA_FILE'),
        now=now,
    )
    if signing['overdue']:
        notices.append({
            'severity': 'warning',
            'message': 'Journeyman session-signing key is overdue for rotation ({} days old).'.format(
                signing['age_days'] if signing['age_days'] is not None else 'unknown'
            ),
        })

    credential_key_timestamp, _ = _active_credential_key_timestamp()
    credential_key_state, credential_key_age = _age_state(credential_key_timestamp, now=now)
    if credential_key_state in {'due_soon', 'overdue'}:
        notices.append({
            'severity': 'warning',
            'message': 'Credential-encryption key is {} ({} days old); rotate it with the credential-key tooling.'.format(
                'overdue' if credential_key_state == 'overdue' else 'due within 30 days',
                credential_key_age,
            ),
        })

    source_rows = SignalSource.query.filter(SignalSource.secret_created_at.is_not(None)).all()
    for source in source_rows:
        state, age = _age_state(source.secret_created_at, now=now)
        if state in {'due_soon', 'overdue'}:
            notices.append({
                'severity': 'warning',
                'message': 'Signal Source "{}" HMAC secret is {} ({} days old); coordinate rotation with the sender.'.format(
                    source.name,
                    'overdue' if state == 'overdue' else 'due within 30 days',
                    age,
                ),
            })
    return notices


def collect_secret_lifecycle_checks(*, now=None):
    now = _as_utc(now or utcnow())
    checks = []
    signing = session_signing_key_status(
        key_path=current_app.config.get('SESSION_SIGNING_KEY_FILE'),
        metadata_path=current_app.config.get('SESSION_SIGNING_KEY_METADATA_FILE'),
        now=now,
    )
    checks.append({
        'name': 'Session signing key',
        'status': 'warning' if signing['overdue'] else ('healthy' if signing['exists'] else 'failed'),
        'summary': (
            'Overdue for rotation.' if signing['overdue'] else
            '{} days old.'.format(signing['age_days']) if signing['exists'] else
            'Managed key file is missing.'
        ),
        'details': 'Minimum age 7 days; rotates automatically on an eligible server boot; overdue at 12 months.',
    })

    timestamp, path = _active_credential_key_timestamp()
    state, age = _age_state(timestamp, now=now)
    checks.append({
        'name': 'Credential encryption key',
        'status': 'warning' if state in {'due_soon', 'overdue', 'unknown'} else 'healthy',
        'summary': (
            'Rotation overdue.' if state == 'overdue' else
            'Rotation due within 30 days.' if state == 'due_soon' else
            '{} days old.'.format(age) if age is not None else
            'Key age cannot be determined.'
        ),
        'details': path,
    })

    due_sources = 0
    overdue_sources = 0
    for source in SignalSource.query.filter(SignalSource.secret_created_at.is_not(None)).all():
        state, _ = _age_state(source.secret_created_at, now=now)
        due_sources += state == 'due_soon'
        overdue_sources += state == 'overdue'
    checks.append({
        'name': 'Signal Source HMAC secrets',
        'status': 'warning' if due_sources or overdue_sources else 'healthy',
        'summary': '{} due within 30 days; {} overdue.'.format(due_sources, overdue_sources),
        'details': '12-month rotation policy; administrator-coordinated to avoid breaking inbound monitoring.',
    })
    return checks
