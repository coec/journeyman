"""Managed lifecycle for Journeyman's Flask session-signing key.

This module deliberately has no Flask or database dependency so it can run as
root before the Journeyman application services start.
"""

import fcntl
import grp
import hashlib
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_KEY_FILE = Path('/etc/journeyman/session-signing.key')
DEFAULT_METADATA_FILE = Path('/etc/journeyman/session-signing-key.json')
MINIMUM_LIFETIME = timedelta(days=7)
OVERDUE_AGE = timedelta(days=365)
BOOT_ROTATION_WINDOW_SECONDS = 5 * 60
UNSAFE_LEGACY_KEYS = {'', 'development-only-change-me', 'CHANGE_ME'}


def utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value):
    return _as_utc(datetime.fromisoformat(str(value)))


def session_key_file():
    return Path(os.environ.get('JOURNEYMAN_SESSION_SIGNING_KEY_FILE', str(DEFAULT_KEY_FILE)))


def session_key_metadata_file():
    return Path(os.environ.get('JOURNEYMAN_SESSION_SIGNING_KEY_METADATA_FILE', str(DEFAULT_METADATA_FILE)))


def current_boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text(encoding='utf-8').strip()
    except OSError:
        return 'unknown'


def current_uptime_seconds():
    try:
        return float(Path('/proc/uptime').read_text(encoding='utf-8').split()[0])
    except (OSError, ValueError, IndexError):
        return float('inf')


def _fingerprint(secret):
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()[:16]


def _journeyman_group_id():
    try:
        return grp.getgrnam('journeyman').gr_gid
    except KeyError:
        return None


def _atomic_write(path, content, *, mode):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        gid = _journeyman_group_id()
        if gid is not None and os.geteuid() == 0:
            os.chown(temporary, 0, gid)
        os.replace(temporary, path)
        directory_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_key(path):
    path = Path(path)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError('Session-signing key path must be a regular file: {}'.format(path))
    if st.st_mode & 0o007:
        raise RuntimeError('Session-signing key must not be accessible by other users: {}'.format(path))
    value = path.read_text(encoding='utf-8').strip()
    if not value:
        raise RuntimeError('Session-signing key file is empty: {}'.format(path))
    return value


def _read_metadata(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError('Session-signing key metadata is invalid: {}'.format(exc)) from exc
    if not isinstance(value, dict):
        raise RuntimeError('Session-signing key metadata must be a JSON object.')
    return value


def _write_metadata(path, *, created_at, rotated_at, boot_id, fingerprint, reason):
    payload = {
        'created_at': _as_utc(created_at).isoformat(),
        'rotated_at': _as_utc(rotated_at).isoformat(),
        'last_rotation_boot_id': str(boot_id or ''),
        'fingerprint': fingerprint,
        'last_rotation_reason': str(reason or ''),
    }
    _atomic_write(path, json.dumps(payload, sort_keys=True, indent=2) + '\n', mode=0o640)
    return payload


def _new_secret():
    return secrets.token_urlsafe(64)


def _initial_metadata_for_existing_key(key_path, metadata_path, now, boot_id):
    st = Path(key_path).stat()
    timestamp = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    secret = _read_key(key_path)
    return _write_metadata(
        metadata_path,
        created_at=timestamp,
        rotated_at=timestamp,
        boot_id='',
        fingerprint=_fingerprint(secret),
        reason='metadata_initialized',
    )


def _with_lock(key_path):
    lock_path = Path(str(key_path) + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, 'a+', encoding='utf-8')
    os.chmod(lock_path, 0o640)
    gid = _journeyman_group_id()
    if gid is not None and os.geteuid() == 0:
        os.chown(lock_path, 0, gid)
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _rotate_locked(key_path, metadata_path, *, now, boot_id, reason, created_at=None):
    secret = _new_secret()
    _atomic_write(key_path, secret + '\n', mode=0o640)
    created_at = created_at or now
    metadata = _write_metadata(
        metadata_path,
        created_at=created_at,
        rotated_at=now,
        boot_id=boot_id,
        fingerprint=_fingerprint(secret),
        reason=reason,
    )
    return metadata


def prepare_session_signing_key(*, key_path=None, metadata_path=None, now=None,
                                uptime_seconds=None, boot_id=None, legacy_secret=None):
    """Ensure a key exists and rotate once after an eligible server boot."""
    key_path = Path(key_path or session_key_file())
    metadata_path = Path(metadata_path or session_key_metadata_file())
    now = _as_utc(now or utcnow())
    uptime_seconds = current_uptime_seconds() if uptime_seconds is None else float(uptime_seconds)
    boot_id = current_boot_id() if boot_id is None else str(boot_id)
    legacy_secret = str(legacy_secret if legacy_secret is not None else os.environ.get('JOURNEYMAN_SECRET_KEY', '')).strip()

    lock = _with_lock(key_path)
    try:
        created = False
        rotated = False
        if not key_path.exists():
            secret = legacy_secret if legacy_secret not in UNSAFE_LEGACY_KEYS else _new_secret()
            _atomic_write(key_path, secret + '\n', mode=0o640)
            metadata = _write_metadata(
                metadata_path,
                created_at=now,
                rotated_at=now,
                boot_id=boot_id,
                fingerprint=_fingerprint(secret),
                reason='legacy_migration' if secret == legacy_secret and legacy_secret else 'initial_generation',
            )
            created = True
        else:
            _read_key(key_path)
            metadata = _read_metadata(metadata_path)
            if metadata is None:
                metadata = _initial_metadata_for_existing_key(key_path, metadata_path, now, boot_id)

        rotated_at = _parse_datetime(metadata['rotated_at'])
        key_age = now - rotated_at
        already_rotated_this_boot = metadata.get('last_rotation_boot_id') == boot_id
        if (
            not created
            and uptime_seconds < BOOT_ROTATION_WINDOW_SECONDS
            and key_age >= MINIMUM_LIFETIME
            and not already_rotated_this_boot
        ):
            metadata = _rotate_locked(
                key_path,
                metadata_path,
                now=now,
                boot_id=boot_id,
                reason='eligible_server_boot',
                created_at=_parse_datetime(metadata['created_at']),
            )
            rotated = True

        return {
            'created': created,
            'rotated': rotated,
            **session_signing_key_status(
                key_path=key_path,
                metadata_path=metadata_path,
                now=now,
            ),
        }
    finally:
        lock.close()


def rotate_session_signing_key(*, key_path=None, metadata_path=None, now=None,
                               boot_id=None, reason='manual'):
    key_path = Path(key_path or session_key_file())
    metadata_path = Path(metadata_path or session_key_metadata_file())
    now = _as_utc(now or utcnow())
    boot_id = current_boot_id() if boot_id is None else str(boot_id)
    lock = _with_lock(key_path)
    try:
        if not key_path.exists():
            raise RuntimeError('Session-signing key does not exist; run prepare first.')
        existing = _read_metadata(metadata_path)
        if existing is None:
            existing = _initial_metadata_for_existing_key(key_path, metadata_path, now, boot_id)
        _rotate_locked(
            key_path,
            metadata_path,
            now=now,
            boot_id=boot_id,
            reason=reason,
            created_at=_parse_datetime(existing['created_at']),
        )
        return session_signing_key_status(key_path=key_path, metadata_path=metadata_path, now=now)
    finally:
        lock.close()


def session_signing_key_status(*, key_path=None, metadata_path=None, now=None):
    key_path = Path(key_path or session_key_file())
    metadata_path = Path(metadata_path or session_key_metadata_file())
    now = _as_utc(now or utcnow())
    if not key_path.exists():
        return {'exists': False, 'age_days': None, 'overdue': True, 'fingerprint': '', 'rotated_at': None}
    metadata = _read_metadata(metadata_path)
    if metadata is None:
        st = key_path.stat()
        rotated_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        fingerprint = _fingerprint(_read_key(key_path))
    else:
        rotated_at = _parse_datetime(metadata['rotated_at'])
        fingerprint = str(metadata.get('fingerprint') or '')
    age = now - rotated_at
    return {
        'exists': True,
        'age_days': max(0, int(age.total_seconds() // 86400)),
        'overdue': age >= OVERDUE_AGE,
        'fingerprint': fingerprint,
        'rotated_at': rotated_at,
    }
