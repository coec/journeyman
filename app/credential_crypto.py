"""
Encryption and decryption of Journeyman credential payloads.
"""

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_CREDENTIAL_KEY_FILE = "/etc/journeyman/credential.key"
DEFAULT_CREDENTIAL_KEYRING_DIR = "/etc/journeyman/credential-keys"
DEFAULT_CREDENTIAL_ACTIVE_KEY_FILE = "/etc/journeyman/credential-keys/active"


class CredentialCryptoError(Exception):
    """
    Base error for credential encryption operations.
    """


class CredentialKeyError(CredentialCryptoError):
    """
    Raised when the credential encryption key cannot be loaded.
    """


class CredentialDecryptError(CredentialCryptoError):
    """
    Raised when an encrypted payload cannot be decrypted.
    """


def credential_key_file():
    """Return the legacy credential encryption-key path."""
    return os.environ.get("JOURNEYMAN_CREDENTIAL_KEY_FILE", DEFAULT_CREDENTIAL_KEY_FILE)


def credential_keyring_dir():
    return Path(os.environ.get("JOURNEYMAN_CREDENTIAL_KEYRING_DIR", DEFAULT_CREDENTIAL_KEYRING_DIR))


def credential_active_key_file():
    return Path(os.environ.get("JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE", DEFAULT_CREDENTIAL_ACTIVE_KEY_FILE))


def _load_key_file(key_file):
    key_file = str(key_file)
    try:
        key_stat = os.stat(key_file)
        if not stat.S_ISREG(key_stat.st_mode):
            raise CredentialKeyError("Credential key file {!r} must be a regular file.".format(key_file))
        if key_stat.st_mode & 0o077:
            raise CredentialKeyError(
                "Credential key file {!r} must not be accessible by group or other users; "
                "set its mode to 0600 or stricter.".format(key_file)
            )
        with open(key_file, "rb") as key_handle:
            key = key_handle.read().strip()
    except CredentialKeyError:
        raise
    except OSError as exc:
        raise CredentialKeyError("Unable to read credential key file {!r}: {}".format(key_file, exc)) from exc
    if not key:
        raise CredentialKeyError("Credential key file {!r} is empty.".format(key_file))
    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise CredentialKeyError("Credential key file {!r} does not contain a valid Fernet key.".format(key_file)) from exc
    return key


def load_credential_key():
    """Load the legacy v1 key. Retained for existing deployments and tests."""
    return _load_key_file(credential_key_file())


def _validate_key_id(key_id):
    key_id = str(key_id or "").strip()
    if not key_id or not key_id.replace("-", "").replace("_", "").isalnum():
        raise CredentialKeyError("Credential key id contains invalid characters.")
    return key_id


def active_credential_key_id():
    path = credential_active_key_file()
    try:
        key_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        # Existing installations remain readable/writable until an administrator
        # explicitly initializes the versioned keyring.
        return None
    return _validate_key_id(key_id)


def load_credential_key_by_id(key_id):
    if key_id is None:
        return load_credential_key()
    key_id = _validate_key_id(key_id)
    return _load_key_file(credential_keyring_dir() / (key_id + ".key"))


def _fernet(key_id=None):
    return Fernet(load_credential_key_by_id(key_id))


def encrypt_credential_data_with_key_id(credential_data):
    if not isinstance(credential_data, dict):
        raise ValueError("Credential data must be a dictionary.")
    key_id = active_credential_key_id()
    serialized = json.dumps(
        credential_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _fernet(key_id).encrypt(serialized), key_id


def encrypt_credential_data(credential_data):
    """
    Serialize and encrypt a credential-data dictionary.

    Returns bytes suitable for Credential.encrypted_data.
    """

    if not isinstance(credential_data, dict):
        raise ValueError(
            "Credential data must be a dictionary."
        )

    serialized = json.dumps(
        credential_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return _fernet(active_credential_key_id()).encrypt(serialized)


def decrypt_credential_data(encrypted_data, key_id=None):
    """
    Decrypt and deserialize a credential payload.

    Returns the original credential-data dictionary.
    """

    if encrypted_data is None:
        return {}

    if not isinstance(
        encrypted_data,
        (bytes, bytearray),
    ):
        raise CredentialDecryptError(
            "Encrypted credential data must be bytes."
        )

    try:
        serialized = _fernet(key_id).decrypt(
            bytes(encrypted_data)
        )
    except InvalidToken as exc:
        raise CredentialDecryptError(
            "Credential data could not be decrypted."
        ) from exc

    try:
        credential_data = json.loads(
            serialized.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CredentialDecryptError(
            "Decrypted credential data is invalid."
        ) from exc

    if not isinstance(credential_data, dict):
        raise CredentialDecryptError(
            "Decrypted credential data is not "
            "a dictionary."
        )

    return credential_data
