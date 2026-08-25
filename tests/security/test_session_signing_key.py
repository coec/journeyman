from datetime import datetime, timedelta, timezone

import pytest

from journeyman_session_key import prepare_session_signing_key, session_signing_key_status

pytestmark = pytest.mark.security


def test_session_key_rotates_once_on_eligible_server_boot(tmp_path):
    key = tmp_path / 'session.key'
    metadata = tmp_path / 'session.json'
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    initial = prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=first,
        uptime_seconds=600, boot_id='boot-a', legacy_secret='legacy-safe-secret-value',
    )
    original = key.read_text(encoding='utf-8')
    assert initial['created'] is True

    second = first + timedelta(days=8)
    rotated = prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=second,
        uptime_seconds=120, boot_id='boot-b',
    )
    assert rotated['rotated'] is True
    assert key.read_text(encoding='utf-8') != original
    once = key.read_text(encoding='utf-8')

    repeated = prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=second + timedelta(seconds=10),
        uptime_seconds=130, boot_id='boot-b',
    )
    assert repeated['rotated'] is False
    assert key.read_text(encoding='utf-8') == once


def test_session_key_does_not_rotate_before_seven_days_or_on_service_restart(tmp_path):
    key = tmp_path / 'session.key'
    metadata = tmp_path / 'session.json'
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=start,
        uptime_seconds=10, boot_id='boot-a', legacy_secret='legacy-safe-secret-value',
    )
    original = key.read_text(encoding='utf-8')

    young = prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=start + timedelta(days=6),
        uptime_seconds=30, boot_id='boot-b',
    )
    assert young['rotated'] is False

    service_restart = prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=start + timedelta(days=20),
        uptime_seconds=3600, boot_id='boot-c',
    )
    assert service_restart['rotated'] is False
    assert key.read_text(encoding='utf-8') == original


def test_session_key_reports_twelve_month_overdue_state(tmp_path):
    key = tmp_path / 'session.key'
    metadata = tmp_path / 'session.json'
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    prepare_session_signing_key(
        key_path=key, metadata_path=metadata, now=start,
        uptime_seconds=600, boot_id='boot-a', legacy_secret='legacy-safe-secret-value',
    )
    status = session_signing_key_status(
        key_path=key, metadata_path=metadata, now=start + timedelta(days=365),
    )
    assert status['overdue'] is True
    assert status['age_days'] == 365
