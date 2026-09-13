"""Lifecycle operations for Journeyman credential storage keys.

This module manages only at-rest credential storage keys.  It is intentionally
separate from the runner PKI/transport lifecycle.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import db
from app.credential_crypto import (
    CredentialDecryptError,
    CredentialKeyError,
    _load_rsa_private_key,
    _load_rsa_public_key,
    _validate_key_id,
    active_credential_key_id,
    credential_active_key_file,
    credential_key_file,
    credential_keyring_dir,
    credential_payload_format_version,
    decrypt_credential_data,
    encrypt_credential_data_with_key_id,
    rewrap_credential_data,
)
from app.models import (
    Credential,
    DirectorySetting,
    EnvironmentBuildSetting,
    JobCredentialSnapshot,
    JobPackageSnapshot,
    NotificationTarget,
    SignalSource,
)

DEFAULT_RSA_KEY_SIZE = 3072
MINIMUM_RSA_KEY_SIZE = 2048


def _utcnow_text():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metadata_path(key_id):
    return credential_keyring_dir() / (_validate_key_id(key_id) + ".json")


def _ensure_keyring():
    keyring = credential_keyring_dir()
    keyring.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(keyring, 0o700)
    return keyring


def _atomic_write(path, data, mode):
    path = Path(path)
    temporary = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(temporary), flags, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_metadata(key_id):
    path = _metadata_path(key_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialKeyError(
            "Unable to read credential storage-key metadata {!r}: {}".format(str(path), exc)
        ) from exc
    if not isinstance(value, dict):
        raise CredentialKeyError(
            "Credential storage-key metadata {!r} is invalid.".format(str(path))
        )
    return value


def _write_metadata(key_id, **updates):
    key_id = _validate_key_id(key_id)
    metadata = _read_metadata(key_id)
    metadata.update(updates)
    metadata["key_id"] = key_id
    encoded = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path = _metadata_path(key_id)
    temporary = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
    try:
        _atomic_write(temporary, encoded, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return metadata


def generate_storage_key(key_id, *, key_size=DEFAULT_RSA_KEY_SIZE):
    """Generate an RSA storage keypair without making it active."""
    key_id = _validate_key_id(key_id)
    key_size = int(key_size)
    if key_size < MINIMUM_RSA_KEY_SIZE:
        raise CredentialKeyError(
            "Credential storage RSA key size must be at least {} bits.".format(
                MINIMUM_RSA_KEY_SIZE
            )
        )

    keyring = _ensure_keyring()
    private_path = keyring / (key_id + ".private.pem")
    public_path = keyring / (key_id + ".public.pem")
    metadata_path = keyring / (key_id + ".json")
    legacy_path = keyring / (key_id + ".key")
    conflicts = [
        path for path in (private_path, public_path, metadata_path, legacy_path)
        if path.exists()
    ]
    if conflicts:
        raise CredentialKeyError(
            "Credential storage key already exists: {}".format(
                ", ".join(str(path) for path in conflicts)
            )
        )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    created = []
    try:
        _atomic_write(private_path, private_pem, 0o600)
        created.append(private_path)
        _atomic_write(public_path, public_pem, 0o644)
        created.append(public_path)
        metadata = {
            "key_id": key_id,
            "key_size": private_key.key_size,
            "created_at": _utcnow_text(),
            "state": "decrypt-only",
        }
        _atomic_write(
            metadata_path,
            (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            0o600,
        )
        created.append(metadata_path)
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise

    return metadata


def activate_storage_key(key_id):
    """Make an existing RSA storage key active for new encryption."""
    key_id = _validate_key_id(key_id)
    _ensure_keyring()

    # Fully load both halves before changing the active pointer.  This validates
    # file permissions, PEM encoding, RSA type, and minimum key size.
    private_key = _load_rsa_private_key(key_id)
    public_key = _load_rsa_public_key(key_id)
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise CredentialKeyError(
            "Credential storage key {!r} public/private key files do not match.".format(key_id)
        )

    previous = active_credential_key_id()
    active_path = credential_active_key_file()
    active_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(active_path.parent, 0o700)
    temporary = active_path.with_name(".{}.tmp-{}".format(active_path.name, os.getpid()))
    _atomic_write(temporary, (key_id + "\n").encode("utf-8"), 0o600)
    os.replace(temporary, active_path)

    now = _utcnow_text()
    current_metadata = _read_metadata(key_id)
    _write_metadata(
        key_id,
        key_size=public_key.key_size,
        created_at=current_metadata.get("created_at") or now,
        state="active",
        activated_at=now,
    )
    if previous and previous != key_id:
        previous_public = credential_keyring_dir() / (previous + ".public.pem")
        if previous_public.is_file():
            previous_metadata = _read_metadata(previous)
            _write_metadata(
                previous,
                state="decrypt-only",
                deactivated_at=now,
                created_at=previous_metadata.get("created_at"),
            )
    return previous


def storage_key_status():
    """Return active and retained RSA storage-key information."""
    keyring = credential_keyring_dir()
    active = active_credential_key_id()
    keys = []
    if not keyring.is_dir():
        return {"active_key_id": active, "keys": keys}

    key_ids = set()
    for path in keyring.glob("*.public.pem"):
        key_ids.add(path.name[:-len(".public.pem")])
    for path in keyring.glob("*.private.pem"):
        key_ids.add(path.name[:-len(".private.pem")])
    for path in keyring.glob("*.json"):
        key_ids.add(path.name[:-len(".json")])

    for key_id in sorted(key_ids):
        try:
            key_id = _validate_key_id(key_id)
        except CredentialKeyError:
            continue
        public_path = keyring / (key_id + ".public.pem")
        private_path = keyring / (key_id + ".private.pem")
        metadata = _read_metadata(key_id)
        key_size = metadata.get("key_size")
        if key_size is None and public_path.is_file():
            try:
                key_size = _load_rsa_public_key(key_id).key_size
            except CredentialKeyError:
                key_size = None
        keys.append(
            {
                "key_id": key_id,
                "state": "active" if key_id == active else "decrypt-only",
                "key_size": key_size,
                "private_key_present": private_path.is_file(),
                "public_key_present": public_path.is_file(),
                "created_at": metadata.get("created_at"),
            }
        )
    return {"active_key_id": active, "keys": keys}


def _encrypted_row_specs():
    """Return all database fields containing credential-crypto payloads."""
    return (
        (Credential, "encrypted_data", "credential_key_id", "secret_format_version"),
        (JobCredentialSnapshot, "encrypted_data", "credential_key_id", "secret_format_version"),
        (NotificationTarget, "encrypted_secret", "secret_key_id", None),
        (SignalSource, "encrypted_hmac_secret", "hmac_secret_key_id", None),
        (DirectorySetting, "encrypted_bind_password", None, None),
        (EnvironmentBuildSetting, "encrypted_proxy_password", None, None),
        (JobPackageSnapshot, "encrypted_extra_vars", None, None),
    )


def _rows(model):
    return db.session.execute(db.select(model)).scalars().all()


def rewrap_v2_rows(new_key_id):
    """Rewrap every v2 database payload to ``new_key_id``.

    No database commit is performed here; the CLI owns transaction boundaries.
    """
    new_key_id = _validate_key_id(new_key_id)
    # Validate target public key before touching rows.
    _load_rsa_public_key(new_key_id)

    counts = {"rewrapped": 0, "already_current": 0, "legacy_skipped": 0}
    for model, encrypted_attr, key_id_attr, format_attr in _encrypted_row_specs():
        for row in _rows(model):
            encrypted = getattr(row, encrypted_attr)
            if encrypted is None:
                continue
            if credential_payload_format_version(encrypted) != 2:
                counts["legacy_skipped"] += 1
                continue
            expected = getattr(row, key_id_attr) if key_id_attr else None
            updated, updated_key_id, changed = rewrap_credential_data(
                encrypted,
                new_key_id,
                expected_key_id=expected,
            )
            if changed:
                setattr(row, encrypted_attr, updated)
                if key_id_attr:
                    setattr(row, key_id_attr, updated_key_id)
                if format_attr:
                    setattr(row, format_attr, 2)
                counts["rewrapped"] += 1
            else:
                counts["already_current"] += 1
    return counts


def _legacy_fernet_key_ids():
    """Return candidate Fernet key ids for rows lacking a key-id column."""
    keyring = credential_keyring_dir()
    if not keyring.is_dir():
        return []
    result = []
    for path in sorted(keyring.glob("*.key")):
        key_id = path.name[:-4]
        try:
            result.append(_validate_key_id(key_id))
        except CredentialKeyError:
            continue
    return result


def _decrypt_legacy_without_key_id(encrypted):
    """Decrypt an old row whose schema has no credential key-id column."""
    errors = []
    # Historically these fields normally used the original credential.key.
    try:
        return decrypt_credential_data(encrypted, None)
    except (CredentialDecryptError, CredentialKeyError) as exc:
        errors.append(exc)

    # If a versioned Fernet keyring had already been enabled, the old schema had
    # no place to record which key protected these settings.  Fernet provides
    # authenticated decryption, so retained .key files can safely be tried.
    for key_id in _legacy_fernet_key_ids():
        try:
            return decrypt_credential_data(encrypted, key_id)
        except (CredentialDecryptError, CredentialKeyError) as exc:
            errors.append(exc)

    detail = str(errors[-1]) if errors else "no legacy Fernet keys are available"
    raise CredentialDecryptError(
        "Unable to decrypt legacy credential payload without stored key metadata: {}".format(detail)
    )


def migrate_legacy_rows():
    """Convert every remaining v1 Fernet database payload to v2 envelopes.

    The currently active storage key must be an RSA key.  No commit is performed;
    the caller owns the transaction.
    """
    active = active_credential_key_id()
    if not active:
        raise CredentialKeyError(
            "No active credential storage key is configured; generate and activate an RSA key first."
        )
    _load_rsa_public_key(active)
    _load_rsa_private_key(active)

    counts = {"migrated": 0, "v2_skipped": 0}
    for model, encrypted_attr, key_id_attr, format_attr in _encrypted_row_specs():
        for row in _rows(model):
            encrypted = getattr(row, encrypted_attr)
            if encrypted is None:
                continue
            if credential_payload_format_version(encrypted) == 2:
                counts["v2_skipped"] += 1
                continue

            if key_id_attr:
                old_key_id = getattr(row, key_id_attr)
                plaintext = decrypt_credential_data(encrypted, old_key_id)
            else:
                plaintext = _decrypt_legacy_without_key_id(encrypted)

            updated, updated_key_id = encrypt_credential_data_with_key_id(plaintext)
            if credential_payload_format_version(updated) != 2:
                raise CredentialKeyError(
                    "Active credential storage key {!r} did not produce a v2 envelope.".format(active)
                )
            setattr(row, encrypted_attr, updated)
            if key_id_attr:
                setattr(row, key_id_attr, updated_key_id)
            if format_attr:
                setattr(row, format_attr, 2)
            counts["migrated"] += 1
    return counts
