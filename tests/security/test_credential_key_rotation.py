"""ASVS evidence for credential-encryption key lifecycle and rotation."""

import os

import pytest
from cryptography.fernet import Fernet

from app.credential_crypto import (
    decrypt_credential_data,
    encrypt_credential_data_with_key_id,
)


pytestmark = pytest.mark.security


def _write_key(path, key):
    path.write_bytes(key + b"\n")
    os.chmod(path, 0o600)


def test_versioned_keyring_encrypts_with_active_key(monkeypatch, tmp_path):
    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_key(keyring / "2026-08.key", Fernet.generate_key())
    active = keyring / "active"
    active.write_text("2026-08\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, key_id = encrypt_credential_data_with_key_id({"password": "secret"})
    assert key_id == "2026-08"
    assert decrypt_credential_data(encrypted, key_id)["password"] == "secret"


def test_old_key_remains_decryptable_after_active_key_changes(monkeypatch, tmp_path):
    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_key(keyring / "old.key", Fernet.generate_key())
    _write_key(keyring / "new.key", Fernet.generate_key())
    active = keyring / "active"
    active.write_text("old\n")
    os.chmod(active, 0o600)
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, old_id = encrypt_credential_data_with_key_id({"password": "before"})
    active.write_text("new\n")
    encrypted_new, new_id = encrypt_credential_data_with_key_id({"password": "after"})

    assert old_id == "old"
    assert new_id == "new"
    assert decrypt_credential_data(encrypted, old_id)["password"] == "before"
    assert decrypt_credential_data(encrypted_new, new_id)["password"] == "after"


def test_keyring_rejects_insecure_key_permissions(monkeypatch, tmp_path):
    keyring = tmp_path / "keys"
    keyring.mkdir()
    key = keyring / "bad.key"
    _write_key(key, Fernet.generate_key())
    os.chmod(key, 0o644)
    active = keyring / "active"
    active.write_text("bad\n")
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    from app.credential_crypto import CredentialKeyError
    with pytest.raises(CredentialKeyError, match="0600 or stricter"):
        encrypt_credential_data_with_key_id({"password": "secret"})
