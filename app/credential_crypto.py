"""
Encryption and decryption of Journeyman credential payloads.

Version 1 payloads are Fernet tokens encrypted with the legacy credential key
or a versioned ``<key-id>.key`` keyring entry.

Version 2 payloads use envelope encryption:

* a fresh random 256-bit AES key encrypts the JSON payload with AES-GCM;
* the active RSA public key wraps that AES key with RSA-OAEP/SHA-256; and
* the envelope carries the key id required to locate the private key.

Keeping the key id inside the v2 envelope allows encrypted settings that do not
have a dedicated database key-id column to remain decryptable after the active
key changes. Legacy Fernet payloads remain readable for in-place upgrades.
"""

import base64
import json
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_CREDENTIAL_KEY_FILE = "/etc/journeyman/credential.key"
DEFAULT_CREDENTIAL_KEYRING_DIR = "/etc/journeyman/credential-keys"
DEFAULT_CREDENTIAL_ACTIVE_KEY_FILE = "/etc/journeyman/credential-keys/active"

V2_ENVELOPE_PREFIX = b"JM-CREDENTIAL-V2\x00"
V2_ENVELOPE_VERSION = 2
V2_CONTENT_ALGORITHM = "AES-256-GCM"
V2_KEY_WRAP_ALGORITHM = "RSA-OAEP-SHA256"
V2_AES_KEY_BYTES = 32
V2_NONCE_BYTES = 12
MINIMUM_RSA_KEY_SIZE = 2048


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


def credential_public_key_file(key_id):
    key_id = _validate_key_id(key_id)
    return credential_keyring_dir() / (key_id + ".public.pem")


def credential_private_key_file(key_id):
    key_id = _validate_key_id(key_id)
    return credential_keyring_dir() / (key_id + ".private.pem")


def _read_key_file(key_file, *, private):
    key_file = str(key_file)
    try:
        key_stat = os.stat(key_file)
        if not stat.S_ISREG(key_stat.st_mode):
            raise CredentialKeyError("Credential key file {!r} must be a regular file.".format(key_file))
        if private and key_stat.st_mode & 0o077:
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
    return key


def _load_key_file(key_file):
    key = _read_key_file(key_file, private=True)
    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise CredentialKeyError(
            "Credential key file {!r} does not contain a valid Fernet key.".format(str(key_file))
        ) from exc
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
    """Load a legacy Fernet key by id."""
    if key_id is None:
        return load_credential_key()
    key_id = _validate_key_id(key_id)
    return _load_key_file(credential_keyring_dir() / (key_id + ".key"))


def _load_rsa_public_key(key_id):
    path = credential_public_key_file(key_id)
    encoded = _read_key_file(path, private=False)
    try:
        key = serialization.load_pem_public_key(encoded)
    except (TypeError, ValueError) as exc:
        raise CredentialKeyError(
            "Credential public key file {!r} does not contain a valid PEM public key.".format(str(path))
        ) from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise CredentialKeyError("Credential public key file {!r} must contain an RSA key.".format(str(path)))
    if key.key_size < MINIMUM_RSA_KEY_SIZE:
        raise CredentialKeyError(
            "Credential public key file {!r} uses an RSA key smaller than {} bits.".format(
                str(path), MINIMUM_RSA_KEY_SIZE
            )
        )
    return key


def _load_rsa_private_key(key_id):
    path = credential_private_key_file(key_id)
    encoded = _read_key_file(path, private=True)
    try:
        key = serialization.load_pem_private_key(encoded, password=None)
    except (TypeError, ValueError) as exc:
        raise CredentialKeyError(
            "Credential private key file {!r} does not contain a valid unencrypted PEM private key.".format(str(path))
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CredentialKeyError("Credential private key file {!r} must contain an RSA key.".format(str(path)))
    if key.key_size < MINIMUM_RSA_KEY_SIZE:
        raise CredentialKeyError(
            "Credential private key file {!r} uses an RSA key smaller than {} bits.".format(
                str(path), MINIMUM_RSA_KEY_SIZE
            )
        )
    return key


def _fernet(key_id=None):
    return Fernet(load_credential_key_by_id(key_id))


def _serialize_credential_data(credential_data):
    if not isinstance(credential_data, dict):
        raise ValueError("Credential data must be a dictionary.")
    return json.dumps(
        credential_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value, field_name):
    if not isinstance(value, str):
        raise CredentialDecryptError("Credential v2 envelope field {!r} is invalid.".format(field_name))
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise CredentialDecryptError("Credential v2 envelope field {!r} is invalid.".format(field_name)) from exc


def _v2_aad(key_id):
    return json.dumps(
        {
            "alg": V2_CONTENT_ALGORITHM,
            "kid": key_id,
            "v": V2_ENVELOPE_VERSION,
            "wrap": V2_KEY_WRAP_ALGORITHM,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _v2_key_available(key_id):
    return credential_public_key_file(key_id).is_file()


def _encrypt_v2(serialized, key_id):
    public_key = _load_rsa_public_key(key_id)
    data_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(V2_NONCE_BYTES)
    aad = _v2_aad(key_id)
    ciphertext = AESGCM(data_key).encrypt(nonce, serialized, aad)
    wrapped_key = public_key.encrypt(
        data_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    envelope = {
        "aad_kid": key_id,
        "alg": V2_CONTENT_ALGORITHM,
        "ciphertext": _b64encode(ciphertext),
        "kid": key_id,
        "nonce": _b64encode(nonce),
        "v": V2_ENVELOPE_VERSION,
        "wrap": V2_KEY_WRAP_ALGORITHM,
        "wrapped_key": _b64encode(wrapped_key),
    }
    return V2_ENVELOPE_PREFIX + json.dumps(
        envelope, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _decrypt_v2(encrypted_data, expected_key_id=None):
    try:
        envelope = json.loads(encrypted_data[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialDecryptError("Credential v2 envelope is invalid.") from exc
    if not isinstance(envelope, dict):
        raise CredentialDecryptError("Credential v2 envelope is invalid.")

    key_id = envelope.get("kid")
    try:
        key_id = _validate_key_id(key_id)
    except CredentialKeyError as exc:
        raise CredentialDecryptError("Credential v2 envelope key id is invalid.") from exc

    aad_key_id = envelope.get("aad_kid", key_id)
    try:
        aad_key_id = _validate_key_id(aad_key_id)
    except CredentialKeyError as exc:
        raise CredentialDecryptError("Credential v2 envelope AAD key id is invalid.") from exc

    if expected_key_id is not None and _validate_key_id(expected_key_id) != key_id:
        raise CredentialDecryptError("Credential v2 envelope key id does not match stored key metadata.")
    if envelope.get("v") != V2_ENVELOPE_VERSION:
        raise CredentialDecryptError("Credential v2 envelope version is unsupported.")
    if envelope.get("alg") != V2_CONTENT_ALGORITHM:
        raise CredentialDecryptError("Credential v2 envelope content algorithm is unsupported.")
    if envelope.get("wrap") != V2_KEY_WRAP_ALGORITHM:
        raise CredentialDecryptError("Credential v2 envelope key-wrap algorithm is unsupported.")

    nonce = _b64decode(envelope.get("nonce"), "nonce")
    if len(nonce) != V2_NONCE_BYTES:
        raise CredentialDecryptError("Credential v2 envelope nonce has an invalid length.")
    wrapped_key = _b64decode(envelope.get("wrapped_key"), "wrapped_key")
    ciphertext = _b64decode(envelope.get("ciphertext"), "ciphertext")

    private_key = _load_rsa_private_key(key_id)
    try:
        data_key = private_key.decrypt(
            wrapped_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except ValueError as exc:
        raise CredentialDecryptError("Credential data key could not be unwrapped.") from exc
    if len(data_key) != V2_AES_KEY_BYTES:
        raise CredentialDecryptError("Credential data key has an invalid length.")

    try:
        return AESGCM(data_key).decrypt(nonce, ciphertext, _v2_aad(aad_key_id))
    except InvalidTag as exc:
        raise CredentialDecryptError("Credential data could not be authenticated.") from exc



def _parse_v2_envelope(encrypted_data):
    if not isinstance(encrypted_data, (bytes, bytearray)):
        raise CredentialDecryptError("Encrypted credential data must be bytes.")
    encrypted_data = bytes(encrypted_data)
    if not encrypted_data.startswith(V2_ENVELOPE_PREFIX):
        raise CredentialDecryptError("Credential payload is not a v2 envelope.")
    try:
        envelope = json.loads(encrypted_data[len(V2_ENVELOPE_PREFIX):].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialDecryptError("Credential v2 envelope is invalid.") from exc
    if not isinstance(envelope, dict):
        raise CredentialDecryptError("Credential v2 envelope is invalid.")
    return envelope


def rewrap_credential_data(encrypted_data, new_key_id, expected_key_id=None):
    """Rewrap a v2 envelope data key without decrypting its secret payload.

    Returns ``(updated_envelope, new_key_id, changed)``.  The AES-GCM
    ciphertext and nonce are preserved byte-for-byte.  Patch-1 envelopes did
    not carry ``aad_kid`` explicitly; for those, the original ``kid`` is the
    authenticated AAD key id and is retained during the first rewrap.
    """
    envelope = _parse_v2_envelope(encrypted_data)
    if envelope.get("v") != V2_ENVELOPE_VERSION:
        raise CredentialDecryptError("Credential v2 envelope version is unsupported.")
    if envelope.get("alg") != V2_CONTENT_ALGORITHM:
        raise CredentialDecryptError("Credential v2 envelope content algorithm is unsupported.")
    if envelope.get("wrap") != V2_KEY_WRAP_ALGORITHM:
        raise CredentialDecryptError("Credential v2 envelope key-wrap algorithm is unsupported.")

    try:
        old_key_id = _validate_key_id(envelope.get("kid"))
        new_key_id = _validate_key_id(new_key_id)
        aad_key_id = _validate_key_id(envelope.get("aad_kid", old_key_id))
    except CredentialKeyError as exc:
        raise CredentialDecryptError("Credential v2 envelope key metadata is invalid.") from exc
    if expected_key_id is not None and _validate_key_id(expected_key_id) != old_key_id:
        raise CredentialDecryptError("Credential v2 envelope key id does not match stored key metadata.")
    if old_key_id == new_key_id:
        return bytes(encrypted_data), new_key_id, False

    wrapped_key = _b64decode(envelope.get("wrapped_key"), "wrapped_key")
    old_private_key = _load_rsa_private_key(old_key_id)
    try:
        data_key = old_private_key.decrypt(
            wrapped_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except ValueError as exc:
        raise CredentialDecryptError("Credential data key could not be unwrapped.") from exc
    if len(data_key) != V2_AES_KEY_BYTES:
        raise CredentialDecryptError("Credential data key has an invalid length.")

    new_public_key = _load_rsa_public_key(new_key_id)
    new_wrapped_key = new_public_key.encrypt(
        data_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    envelope["kid"] = new_key_id
    envelope["aad_kid"] = aad_key_id
    envelope["wrapped_key"] = _b64encode(new_wrapped_key)
    updated = V2_ENVELOPE_PREFIX + json.dumps(
        envelope, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return updated, new_key_id, True

def credential_payload_format_version(encrypted_data):
    """Return the on-disk credential payload format version."""
    if isinstance(encrypted_data, (bytes, bytearray)) and bytes(encrypted_data).startswith(V2_ENVELOPE_PREFIX):
        return V2_ENVELOPE_VERSION
    return 1


def encrypt_credential_data_with_key_id(credential_data):
    serialized = _serialize_credential_data(credential_data)
    key_id = active_credential_key_id()
    if key_id is not None and _v2_key_available(key_id):
        return _encrypt_v2(serialized, key_id), key_id
    return _fernet(key_id).encrypt(serialized), key_id


def encrypt_credential_data(credential_data):
    """
    Serialize and encrypt a credential-data dictionary.

    Returns bytes suitable for Credential.encrypted_data.
    """

    encrypted, _key_id = encrypt_credential_data_with_key_id(credential_data)
    return encrypted


def decrypt_credential_data(encrypted_data, key_id=None):
    """
    Decrypt and deserialize a credential payload.

    Version 2 envelopes contain their own key id. ``key_id`` is treated as
    expected metadata when supplied. Non-v2 payloads are interpreted as legacy
    Fernet tokens and continue to use the caller/database key id.

    Returns the original credential-data dictionary.
    """

    if encrypted_data is None:
        return {}

    if not isinstance(encrypted_data, (bytes, bytearray)):
        raise CredentialDecryptError("Encrypted credential data must be bytes.")

    encrypted_data = bytes(encrypted_data)
    if encrypted_data.startswith(V2_ENVELOPE_PREFIX):
        serialized = _decrypt_v2(encrypted_data, key_id)
    else:
        try:
            serialized = _fernet(key_id).decrypt(encrypted_data)
        except InvalidToken as exc:
            raise CredentialDecryptError("Credential data could not be decrypted.") from exc

    try:
        credential_data = json.loads(serialized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialDecryptError("Decrypted credential data is invalid.") from exc

    if not isinstance(credential_data, dict):
        raise CredentialDecryptError("Decrypted credential data is not a dictionary.")

    return credential_data
