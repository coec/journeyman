"""ASVS evidence for credential-encryption key lifecycle and rotation."""

import base64
import os
import stat

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


def _write_rsa_keypair(keyring, key_id):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = keyring / (key_id + ".private.pem")
    public_path = keyring / (key_id + ".public.pem")
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)


def test_v2_envelope_encrypts_with_active_rsa_public_key(monkeypatch, tmp_path):
    from app.credential_crypto import V2_ENVELOPE_PREFIX

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "storage-2026")
    active = keyring / "active"
    active.write_text("storage-2026\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, key_id = encrypt_credential_data_with_key_id({"password": "v2-secret"})

    assert key_id == "storage-2026"
    assert encrypted.startswith(V2_ENVELOPE_PREFIX)
    assert b"v2-secret" not in encrypted
    assert decrypt_credential_data(encrypted, key_id) == {"password": "v2-secret"}


def test_v2_envelope_is_self_describing_when_database_has_no_key_id(monkeypatch, tmp_path):
    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "storage-2026")
    active = keyring / "active"
    active.write_text("storage-2026\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, _key_id = encrypt_credential_data_with_key_id({"token": "self-describing"})
    active.write_text("different-active-key\n")

    assert decrypt_credential_data(encrypted) == {"token": "self-describing"}


def test_v2_envelope_rejects_tampered_ciphertext(monkeypatch, tmp_path):
    import json
    from app.credential_crypto import CredentialDecryptError, V2_ENVELOPE_PREFIX

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "storage-2026")
    active = keyring / "active"
    active.write_text("storage-2026\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, key_id = encrypt_credential_data_with_key_id({"password": "secret"})
    envelope = json.loads(encrypted[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))
    ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.urlsafe_b64encode(bytes(ciphertext)).decode("ascii")
    tampered = V2_ENVELOPE_PREFIX + json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(CredentialDecryptError, match="authenticated"):
        decrypt_credential_data(tampered, key_id)


def test_v2_envelope_rejects_key_id_metadata_mismatch(monkeypatch, tmp_path):
    from app.credential_crypto import CredentialDecryptError

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "storage-2026")
    active = keyring / "active"
    active.write_text("storage-2026\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, _key_id = encrypt_credential_data_with_key_id({"password": "secret"})

    with pytest.raises(CredentialDecryptError, match="does not match stored key metadata"):
        decrypt_credential_data(encrypted, "wrong-key")


def test_v2_private_key_rejects_insecure_permissions(monkeypatch, tmp_path):
    from app.credential_crypto import CredentialKeyError

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "storage-2026")
    os.chmod(keyring / "storage-2026.private.pem", 0o644)
    active = keyring / "active"
    active.write_text("storage-2026\n")
    os.chmod(active, 0o600)

    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, key_id = encrypt_credential_data_with_key_id({"password": "secret"})
    with pytest.raises(CredentialKeyError, match="0600 or stricter"):
        decrypt_credential_data(encrypted, key_id)


def test_v2_rewrap_preserves_ciphertext_and_nonce(monkeypatch, tmp_path):
    import json
    from app.credential_crypto import V2_ENVELOPE_PREFIX, rewrap_credential_data

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "old-storage")
    _write_rsa_keypair(keyring, "new-storage")
    active = keyring / "active"
    active.write_text("old-storage\n")
    os.chmod(active, 0o600)
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, old_id = encrypt_credential_data_with_key_id({"password": "secret"})
    before = json.loads(encrypted[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))

    rewrapped, new_id, changed = rewrap_credential_data(
        encrypted, "new-storage", expected_key_id=old_id
    )
    after = json.loads(rewrapped[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))

    assert changed is True
    assert new_id == "new-storage"
    assert after["kid"] == "new-storage"
    assert after["aad_kid"] == "old-storage"
    assert after["ciphertext"] == before["ciphertext"]
    assert after["nonce"] == before["nonce"]
    assert after["wrapped_key"] != before["wrapped_key"]
    assert decrypt_credential_data(rewrapped, "new-storage") == {"password": "secret"}


def test_v2_rewrap_accepts_patch1_envelope_without_explicit_aad_kid(monkeypatch, tmp_path):
    import json
    from app.credential_crypto import V2_ENVELOPE_PREFIX, rewrap_credential_data

    keyring = tmp_path / "keys"
    keyring.mkdir()
    _write_rsa_keypair(keyring, "old-storage")
    _write_rsa_keypair(keyring, "new-storage")
    active = keyring / "active"
    active.write_text("old-storage\n")
    os.chmod(active, 0o600)
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    encrypted, old_id = encrypt_credential_data_with_key_id({"token": "patch1"})
    envelope = json.loads(encrypted[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))
    envelope.pop("aad_kid", None)
    patch1_envelope = V2_ENVELOPE_PREFIX + json.dumps(
        envelope, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    assert decrypt_credential_data(patch1_envelope, old_id) == {"token": "patch1"}
    rewrapped, new_id, changed = rewrap_credential_data(patch1_envelope, "new-storage")
    assert changed is True
    assert decrypt_credential_data(rewrapped, new_id) == {"token": "patch1"}


def test_storage_key_generation_starts_decrypt_only_and_can_be_activated(monkeypatch, tmp_path):
    from app.services.credential_key_lifecycle import (
        activate_storage_key,
        generate_storage_key,
        storage_key_status,
    )

    keyring = tmp_path / "keys"
    active = keyring / "active"
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    metadata = generate_storage_key("storage-2026", key_size=2048)
    assert metadata["state"] == "decrypt-only"
    assert stat.S_IMODE((keyring / "storage-2026.private.pem").stat().st_mode) == 0o600

    activate_storage_key("storage-2026")
    status = storage_key_status()
    assert status["active_key_id"] == "storage-2026"
    assert status["keys"][0]["state"] == "active"


def test_legacy_credential_can_be_migrated_to_v2(app, monkeypatch, tmp_path):
    from app import db
    from app.models import Credential
    from app.services.credential_key_lifecycle import (
        activate_storage_key,
        generate_storage_key,
        migrate_legacy_rows,
    )

    keyring = tmp_path / "keys"
    active = keyring / "active"
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", str(keyring))
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", str(active))

    with app.app_context():
        credential = Credential(name="legacy", owner="admin", credential_type="machine", username="root",)
        credential.set_credential_data({"password": "old-secret"})
        assert credential.secret_format_version == 1
        db.session.add(credential)
        db.session.commit()

        generate_storage_key("storage-2026", key_size=2048)
        activate_storage_key("storage-2026")
        counts = migrate_legacy_rows()
        db.session.commit()

        db.session.refresh(credential)
        assert counts["migrated"] >= 1
        assert credential.secret_format_version == 2
        assert credential.credential_key_id == "storage-2026"
        assert credential.get_credential_data() == {"password": "old-secret"}
