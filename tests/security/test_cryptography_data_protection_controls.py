import base64
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from app import db
from app.credential_crypto import (
    CredentialDecryptError,
    CredentialKeyError,
    decrypt_credential_data,
    encrypt_credential_data,
    load_credential_key,
)
from app.models import Credential, Runner
from app.services.runner_execution_data import encrypt_execution_data
from app.services.runners import issue_registration_token, register_runner


pytestmark = pytest.mark.security


def test_credential_key_rejects_group_or_world_access(monkeypatch, tmp_path):
    key_path = tmp_path / "credential.key"
    key_path.write_bytes(Fernet.generate_key() + b"\n")
    os.chmod(key_path, 0o644)
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEY_FILE", str(key_path))

    with pytest.raises(CredentialKeyError, match="0600 or stricter"):
        load_credential_key()


def test_credential_ciphertext_is_authenticated_and_contains_no_plaintext(monkeypatch, tmp_path):
    key_path = tmp_path / "credential.key"
    key_path.write_bytes(Fernet.generate_key() + b"\n")
    os.chmod(key_path, 0o600)
    monkeypatch.setenv("JOURNEYMAN_CREDENTIAL_KEY_FILE", str(key_path))

    secret = "JM-crypto-canary-298341"
    encrypted = encrypt_credential_data({"password": secret})
    assert secret.encode("utf-8") not in encrypted
    assert decrypt_credential_data(encrypted)["password"] == secret

    tampered = bytearray(encrypted)
    tampered[len(tampered) // 2] ^= 1
    with pytest.raises(CredentialDecryptError):
        decrypt_credential_data(bytes(tampered))


def test_remote_execution_envelope_uses_authenticated_aes_256_gcm():
    job = SimpleNamespace(
        id=8123,
        credential_snapshots=[],
        package_snapshot=None,
        inventory_snapshots=[],
        steps=[],
    )
    runner = SimpleNamespace(runner_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    token = "dispatch-token-with-high-entropy-test-material"

    envelope = encrypt_execution_data(job, runner, token)
    assert envelope["algorithm"] == "AES-256-GCM"
    assert envelope["kdf"] == "HKDF-SHA256"

    decode = base64.urlsafe_b64decode
    salt = decode(envelope["salt"])
    nonce = decode(envelope["nonce"])
    ciphertext = bytearray(decode(envelope["ciphertext"]))
    aad = decode(envelope["aad"])

    from app.services.runner_execution_data import _derive_key

    key = _derive_key(token, salt)
    assert len(key) == 32
    ciphertext[-1] ^= 1
    with pytest.raises(InvalidTag):
        AESGCM(key).decrypt(nonce, bytes(ciphertext), aad)


def test_remote_execution_envelopes_use_fresh_salt_and_nonce():
    job = SimpleNamespace(
        id=8124,
        credential_snapshots=[],
        package_snapshot=None,
        inventory_snapshots=[],
        steps=[],
    )
    runner = SimpleNamespace(runner_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    token = "another-dispatch-token-with-high-entropy-material"

    first = encrypt_execution_data(job, runner, token)
    second = encrypt_execution_data(job, runner, token)
    assert first["salt"] != second["salt"]
    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]


def test_runner_bootstrap_secrets_are_random_and_only_digests_are_stored(app):
    with app.app_context():
        runner = Runner(name="crypto-runner", hostname="crypto-runner.example", enabled=True)
        db.session.add(runner)
        db.session.flush()

        registration_token = issue_registration_token(runner)
        assert len(registration_token) >= 40
        assert registration_token not in runner.registration_token_digest
        assert len(runner.registration_token_digest) == 64
        db.session.commit()

        registered, api_secret = register_runner(
            registration_token,
            hostname="crypto-runner.example",
            version="test",
        )
        assert registered.id == runner.id
        assert len(api_secret) >= 60
        assert api_secret not in registered.api_secret_digest
        assert len(registered.api_secret_digest) == 64
        assert registered.registration_token_digest == ""


def test_dispatch_secret_comparisons_use_constant_time_comparison():
    from app.services import runner_job_lifecycle, runner_slice_lifecycle

    job_source = inspect.getsource(runner_job_lifecycle.assignment_matches)
    slice_source = inspect.getsource(runner_slice_lifecycle.assignment_matches)
    assert "secrets.compare_digest" in job_source
    assert "secrets.compare_digest" in slice_source
    assert "dispatch_token == token" not in job_source
    assert "dispatch_token == token" not in slice_source



def test_fallback_admin_cli_uses_scrypt_password_hashing():
    import app.cli as cli

    source = inspect.getsource(cli)
    assert 'generate_password_hash(password, method="scrypt")' in source

def test_client_templates_do_not_use_browser_storage_for_sensitive_state():
    template_root = Path("app/templates")
    static_root = Path("app/static")
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in (template_root, static_root)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".html", ".js"}
    )
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "indexedDB" not in text


def test_client_templates_do_not_load_third_party_tracking_scripts():
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path("app/templates").rglob("*.html")
    ).lower()
    for marker in (
        "google-analytics",
        "googletagmanager",
        "facebook.com/tr",
        "segment.com",
        "mixpanel",
    ):
        assert marker not in text
