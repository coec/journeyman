"""Journeyman-managed PKI for remote runner identities.

This trust domain is deliberately separate from Journeyman's public web TLS
certificate and from the at-rest credential-encryption keyring.

The CA certificate is valid for 52 weeks and is renewed after 39 weeks using
the *same* CA private key. Routine CA private-key rotation is intentionally not
part of the automatic lifecycle. Runner certificates are valid for 30 days.
"""

import ipaddress
import json
import os
import pwd
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from flask import current_app


CA_COMMON_NAME = "Journeyman Runner CA"
RUNNER_URI_PREFIX = "urn:journeyman:runner:"
DEFAULT_CA_KEY_SIZE = 4096
MINIMUM_RUNNER_RSA_KEY_SIZE = 2048
_CLOCK_SKEW = timedelta(minutes=5)
_DNS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class RunnerPkiError(RuntimeError):
    """Raised when runner PKI material or a CSR is invalid."""


def _utcnow():
    return datetime.now(timezone.utc)


def _aware(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cert_not_before(cert):
    value = getattr(cert, "not_valid_before_utc", None)
    return value if value is not None else _aware(cert.not_valid_before)


def _cert_not_after(cert):
    value = getattr(cert, "not_valid_after_utc", None)
    return value if value is not None else _aware(cert.not_valid_after)


def _paths():
    return {
        "root": Path(current_app.config["RUNNER_PKI_ROOT"]),
        "key": Path(current_app.config["RUNNER_CA_PRIVATE_KEY_PATH"]),
        "cert": Path(current_app.config["RUNNER_CA_CERTIFICATE_PATH"]),
        "metadata": Path(current_app.config["RUNNER_CA_METADATA_PATH"]),
    }


def _ensure_private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            account = pwd.getpwnam("journeyman")
        except KeyError:
            return
        os.chown(path, account.pw_uid, account.pw_gid)


def _atomic_write(path, data, mode):
    _ensure_private_directory(path.parent)
    temporary = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _give_private_key_to_service_account(path):
    """When run as root, make the CA key readable only by Journeyman.

    Production services run as the ``journeyman`` account. Development/test
    systems without that account keep the current owner.
    """

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        account = pwd.getpwnam("journeyman")
    except KeyError:
        return
    os.chown(path, account.pw_uid, account.pw_gid)
    os.chmod(path, 0o600)


def _fingerprint_sha256(cert):
    return cert.fingerprint(hashes.SHA256()).hex()


def _metadata_for(cert, *, created_at=None, renewed_at=None):
    now = _utcnow()
    return {
        "subject": cert.subject.rfc4514_string(),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": _fingerprint_sha256(cert),
        "not_before": _cert_not_before(cert).isoformat(),
        "not_after": _cert_not_after(cert).isoformat(),
        "created_at": (created_at or now).isoformat(),
        "renewed_at": renewed_at.isoformat() if renewed_at else None,
        "private_key_rotation": "manual-only",
    }


def _write_metadata(path, metadata):
    payload = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(path, payload, 0o640)


def _build_ca_certificate(private_key, *, now=None):
    now = now or _utcnow()
    validity_days = int(current_app.config["RUNNER_CA_VALIDITY_DAYS"])
    if validity_days < 30:
        raise RunnerPkiError("Runner CA validity must be at least 30 days.")
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(private_key.public_key()),
            critical=False,
        )
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )


def generate_runner_ca(*, key_size=DEFAULT_CA_KEY_SIZE, now=None):
    """Create the Journeyman runner CA. Existing material is never overwritten."""

    paths = _paths()
    existing = [str(paths[name]) for name in ("key", "cert") if paths[name].exists()]
    if existing:
        raise RunnerPkiError(
            "Runner CA already exists; refusing to overwrite: {}".format(", ".join(existing))
        )
    if int(key_size) < 3072:
        raise RunnerPkiError("Runner CA RSA key size must be at least 3072 bits.")

    now = now or _utcnow()
    key = rsa.generate_private_key(public_exponent=65537, key_size=int(key_size))
    cert = _build_ca_certificate(key, now=now)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    _atomic_write(paths["key"], key_pem, 0o600)
    _give_private_key_to_service_account(paths["key"])
    _atomic_write(paths["cert"], cert_pem, 0o644)
    metadata = _metadata_for(cert, created_at=now)
    _write_metadata(paths["metadata"], metadata)
    return metadata


def _load_ca_private_key():
    path = _paths()["key"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunnerPkiError("Unable to read runner CA private key {}: {}".format(path, exc)) from exc
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (TypeError, ValueError) as exc:
        raise RunnerPkiError("Runner CA private key is invalid: {}".format(path)) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RunnerPkiError("Runner CA private key must be RSA.")
    return key


def load_runner_ca_certificate():
    path = _paths()["cert"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunnerPkiError("Unable to read runner CA certificate {}: {}".format(path, exc)) from exc
    try:
        cert = x509.load_pem_x509_certificate(data)
    except ValueError as exc:
        raise RunnerPkiError("Runner CA certificate is invalid: {}".format(path)) from exc
    try:
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound as exc:
        raise RunnerPkiError("Runner CA certificate lacks BasicConstraints.") from exc
    if not constraints.ca:
        raise RunnerPkiError("Runner CA certificate is not a CA certificate.")
    return cert


def runner_ca_status(*, now=None):
    """Return CA health and whether the 39-week certificate renewal point has passed."""

    now = now or _utcnow()
    paths = _paths()
    if not paths["key"].exists() and not paths["cert"].exists():
        return {"initialized": False, "renewal_due": False}
    if not paths["key"].exists() or not paths["cert"].exists():
        raise RunnerPkiError("Runner CA is incomplete; both private key and certificate are required.")

    key = _load_ca_private_key()
    cert = load_runner_ca_certificate()
    ca_public = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_public = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if ca_public != key_public:
        raise RunnerPkiError("Runner CA private key does not match the CA certificate.")

    not_before = _cert_not_before(cert)
    not_after = _cert_not_after(cert)
    renew_after = not_before + timedelta(
        days=int(current_app.config["RUNNER_CA_RENEW_AFTER_DAYS"])
    )
    return {
        "initialized": True,
        "subject": cert.subject.rfc4514_string(),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": _fingerprint_sha256(cert),
        "not_before": not_before,
        "not_after": not_after,
        "renew_after": renew_after,
        "renewal_due": now >= renew_after,
        "expired": now >= not_after,
        "key_size": key.key_size,
        "private_key_path": str(paths["key"]),
        "certificate_path": str(paths["cert"]),
    }


def renew_runner_ca_certificate(*, force=False, now=None):
    """Renew only the CA certificate, deliberately retaining the same private key."""

    now = now or _utcnow()
    status = runner_ca_status(now=now)
    if not status["initialized"]:
        raise RunnerPkiError("Runner CA has not been initialized.")
    if not force and not status["renewal_due"]:
        raise RunnerPkiError(
            "Runner CA renewal is not due until {}.".format(status["renew_after"].isoformat())
        )

    key = _load_ca_private_key()
    old_public = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    cert = _build_ca_certificate(key, now=now)
    new_public = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if old_public != new_public:  # defensive invariant
        raise RunnerPkiError("Runner CA renewal unexpectedly changed the public key.")

    paths = _paths()
    _atomic_write(paths["cert"], cert.public_bytes(serialization.Encoding.PEM), 0o644)
    created_at = None
    try:
        previous = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        if previous.get("created_at"):
            created_at = datetime.fromisoformat(previous["created_at"])
    except (OSError, ValueError, TypeError):
        pass
    metadata = _metadata_for(cert, created_at=created_at or now, renewed_at=now)
    _write_metadata(paths["metadata"], metadata)
    return metadata


def _validate_runner_csr(csr):
    if not csr.is_signature_valid:
        raise RunnerPkiError("Runner CSR signature is invalid.")
    public_key = csr.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise RunnerPkiError("Runner CSR public key must be RSA.")
    if public_key.key_size < MINIMUM_RUNNER_RSA_KEY_SIZE:
        raise RunnerPkiError(
            "Runner CSR RSA key must be at least {} bits.".format(MINIMUM_RUNNER_RSA_KEY_SIZE)
        )
    return public_key


def _runner_san(runner_uuid, hostname=""):
    names = [x509.UniformResourceIdentifier(RUNNER_URI_PREFIX + str(runner_uuid))]
    hostname = str(hostname or "").strip().rstrip(".")
    if hostname:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(hostname)))
        except ValueError:
            if not _DNS_RE.fullmatch(hostname) or ".." in hostname:
                raise RunnerPkiError("Runner hostname is not a valid DNS name: {}".format(hostname))
            names.append(x509.DNSName(hostname.lower()))
    return x509.SubjectAlternativeName(names)


def sign_runner_csr(csr_pem, *, runner_uuid, hostname="", now=None):
    """Sign a runner CSR using a constrained Journeyman runner profile.

    CSR-provided subject/SAN extensions are intentionally not copied. Journeyman
    constructs the identity from the registered runner UUID and hostname so a
    CSR cannot request arbitrary names or CA privileges.
    """

    if not str(runner_uuid or "").strip():
        raise RunnerPkiError("Runner UUID is required for certificate issuance.")
    try:
        csr = x509.load_pem_x509_csr(
            csr_pem.encode("utf-8") if isinstance(csr_pem, str) else bytes(csr_pem)
        )
    except (TypeError, ValueError) as exc:
        raise RunnerPkiError("Runner CSR is not valid PEM.") from exc
    public_key = _validate_runner_csr(csr)
    ca_key = _load_ca_private_key()
    ca_cert = load_runner_ca_certificate()
    status = runner_ca_status(now=now)
    if status["expired"]:
        raise RunnerPkiError("Runner CA certificate is expired; renew it before issuing runner certificates.")

    now = now or _utcnow()
    validity_days = int(current_app.config["RUNNER_CERTIFICATE_VALIDITY_DAYS"])
    if validity_days < 1 or validity_days > 90:
        raise RunnerPkiError("Runner certificate validity must be between 1 and 90 days.")
    requested_not_after = now + timedelta(days=validity_days)
    not_after = min(requested_not_after, _cert_not_after(ca_cert))
    if not_after <= now:
        raise RunnerPkiError("Runner CA expires too soon to issue a runner certificate.")

    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Journeyman Runner {}".format(runner_uuid))]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(_runner_san(runner_uuid, hostname), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    cert = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return {
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        "ca_certificate_pem": ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": _fingerprint_sha256(cert),
        "not_before": _cert_not_before(cert),
        "not_after": _cert_not_after(cert),
        "subject": cert.subject.rfc4514_string(),
    }


def issue_runner_certificate(runner, csr_pem, *, commit=True, now=None):
    """Issue a certificate and persist the expected identity on ``runner``."""

    from app import db

    result = sign_runner_csr(
        csr_pem,
        runner_uuid=runner.runner_uuid,
        hostname=runner.hostname,
        now=now,
    )
    runner.pki_certificate_serial = result["serial"]
    runner.pki_certificate_fingerprint_sha256 = result["fingerprint_sha256"]
    runner.pki_certificate_not_before_at = result["not_before"]
    runner.pki_certificate_not_after_at = result["not_after"]
    runner.pki_certificate_issued_at = now or _utcnow()
    # Successful explicit issuance/re-enrolment is the supported recovery path
    # from a certificate-identity quarantine.
    runner.pki_quarantined = False
    runner.pki_quarantine_reason = ""
    runner.pki_quarantined_at = None
    if commit:
        db.session.commit()
    return result

CONTROLLER_URI = "urn:journeyman:controller"


def ensure_controller_client_identity(*, key_size=3072, now=None, force=False):
    """Create the controller's mTLS client identity for runner management.

    This identity is separate from Journeyman's public web TLS certificate and
    from every runner identity.  The private key never leaves the controller.
    """

    key_path = Path(current_app.config["RUNNER_CONTROLLER_PRIVATE_KEY_PATH"])
    cert_path = Path(current_app.config["RUNNER_CONTROLLER_CERTIFICATE_PATH"])
    if key_path.exists() or cert_path.exists():
        if not (key_path.exists() and cert_path.exists()):
            raise RunnerPkiError(
                "Runner controller identity is incomplete; both key and certificate are required."
            )
        if not force:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            return {
                "created": False,
                "private_key_path": str(key_path),
                "certificate_path": str(cert_path),
                "serial": format(cert.serial_number, "x"),
                "fingerprint_sha256": _fingerprint_sha256(cert),
                "not_after": _cert_not_after(cert),
            }

    if int(key_size) < 2048:
        raise RunnerPkiError("Controller client RSA key size must be at least 2048 bits.")
    ca_key = _load_ca_private_key()
    ca_cert = load_runner_ca_certificate()
    status = runner_ca_status(now=now)
    if status["expired"]:
        raise RunnerPkiError("Runner CA certificate is expired.")

    now = now or _utcnow()
    key = rsa.generate_private_key(public_exponent=65537, key_size=int(key_size))
    requested_not_after = now + timedelta(days=int(current_app.config["RUNNER_CERTIFICATE_VALIDITY_DAYS"]))
    not_after = min(requested_not_after, _cert_not_after(ca_cert))
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Journeyman Controller")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(CONTROLLER_URI)]),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )
    _atomic_write(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        0o600,
    )
    _give_private_key_to_service_account(key_path)
    _atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return {
        "created": True,
        "private_key_path": str(key_path),
        "certificate_path": str(cert_path),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": _fingerprint_sha256(cert),
        "not_after": _cert_not_after(cert),
    }


def controller_client_identity_status(*, now=None):
    """Return controller mTLS certificate expiry/renewal state."""

    cert_path = Path(current_app.config["RUNNER_CONTROLLER_CERTIFICATE_PATH"])
    key_path = Path(current_app.config["RUNNER_CONTROLLER_PRIVATE_KEY_PATH"])
    if not cert_path.is_file() or not key_path.is_file():
        return {"initialized": False, "renewal_due": True}
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RunnerPkiError("Runner controller certificate could not be read.") from exc
    now = now or _utcnow()
    not_after = _cert_not_after(cert)
    renew_before = timedelta(
        days=int(current_app.config["RUNNER_CERTIFICATE_RENEW_BEFORE_DAYS"])
    )
    return {
        "initialized": True,
        "not_after": not_after,
        "renewal_due": now >= not_after - renew_before,
        "expired": now >= not_after,
        "fingerprint_sha256": _fingerprint_sha256(cert),
    }


def renew_controller_client_identity(*, now=None):
    """Renew the controller certificate while deliberately retaining its key."""

    key_path = Path(current_app.config["RUNNER_CONTROLLER_PRIVATE_KEY_PATH"])
    cert_path = Path(current_app.config["RUNNER_CONTROLLER_CERTIFICATE_PATH"])
    if not key_path.is_file() or not cert_path.is_file():
        return ensure_controller_client_identity(now=now)
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise RunnerPkiError("Runner controller private key could not be read.") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RunnerPkiError("Runner controller private key must be RSA.")

    ca_key = _load_ca_private_key()
    ca_cert = load_runner_ca_certificate()
    status = runner_ca_status(now=now)
    if status["expired"]:
        raise RunnerPkiError("Runner CA certificate is expired.")
    now = now or _utcnow()
    requested_not_after = now + timedelta(
        days=int(current_app.config["RUNNER_CERTIFICATE_VALIDITY_DAYS"])
    )
    not_after = min(requested_not_after, _cert_not_after(ca_cert))
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Journeyman Controller")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(CONTROLLER_URI)]),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )
    _atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return {
        "created": False,
        "renewed": True,
        "private_key_path": str(key_path),
        "certificate_path": str(cert_path),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": _fingerprint_sha256(cert),
        "not_after": _cert_not_after(cert),
    }
