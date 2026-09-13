"""Authenticate remote-runner requests at the Journeyman web boundary.

PKI-enrolled runners must authenticate with a client certificate verified by
Nginx against the Journeyman runner CA.  A narrowly-scoped legacy bearer path
is retained only for runners that have not yet been enrolled into runner PKI.
"""

from datetime import datetime, timezone
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from flask import request

from app import db
from app.models import Runner
from app.services.audit import record_audit_event
from app.services.runner_pki import load_runner_ca_certificate
from app.services.runners import authenticate_runner


_TLS_VERIFY_HEADER = "X-Journeyman-TLS-Client-Verify"
_TLS_CERT_HEADER = "X-Journeyman-TLS-Client-Cert"
_RUNNER_ID_HEADER = "X-Journeyman-Runner-ID"


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


def _presented_certificate():
    if str(request.headers.get(_TLS_VERIFY_HEADER) or "").strip() != "SUCCESS":
        return None
    encoded = str(request.headers.get(_TLS_CERT_HEADER) or "").strip()
    if not encoded:
        return None
    try:
        pem = unquote(encoded).encode("ascii")
        return x509.load_pem_x509_certificate(pem)
    except (UnicodeEncodeError, ValueError):
        return None


def _certificate_is_signed_by_runner_ca(cert):
    try:
        ca = load_runner_ca_certificate()
    except Exception:
        return False
    if cert.issuer != ca.subject:
        return False
    public_key = ca.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        return False
    try:
        public_key.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )
    except Exception:
        return False
    return True


def _certificate_matches_runner(cert, runner):
    now = datetime.now(timezone.utc)
    if now < _cert_not_before(cert) or now >= _cert_not_after(cert):
        return False
    if not _certificate_is_signed_by_runner_ca(cert):
        return False

    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return False
    if basic.ca:
        return False

    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return False
    if ExtendedKeyUsageOID.CLIENT_AUTH not in eku:
        return False

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = set(san.get_values_for_type(x509.UniformResourceIdentifier))
    except x509.ExtensionNotFound:
        return False
    expected_uri = "urn:journeyman:runner:{}".format(runner.runner_uuid)
    if expected_uri not in uris:
        return False

    expected_serial = str(runner.pki_certificate_serial or "").strip().lower()
    actual_serial = format(cert.serial_number, "x").lower()
    if not expected_serial or actual_serial != expected_serial:
        return False

    expected_fingerprint = str(
        runner.pki_certificate_fingerprint_sha256 or ""
    ).strip().lower().replace(":", "")
    actual_fingerprint = cert.fingerprint(hashes.SHA256()).hex().lower()
    if not expected_fingerprint or actual_fingerprint != expected_fingerprint:
        return False

    return True


def _retire_legacy_bearer_secret(runner):
    if not str(runner.api_secret_digest or ""):
        return
    runner.api_secret_digest = ""
    db.session.commit()
    record_audit_event(
        "runner.bearer_secret_retired",
        object_type="runner",
        object_id=str(runner.id),
        object_name=runner.name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-mtls",
        details={"runner_uuid": runner.runner_uuid},
    )


def authenticate_runner_request(*, allow_legacy_unenrolled=False):
    """Authenticate the current request as a registered remote runner.

    Once a runner has an issued certificate fingerprint, bearer authentication
    is never accepted for it.  Legacy bearer authentication is available only
    to genuinely pre-PKI runners during the rolling v2 upgrade window.
    """

    runner_uuid = str(request.headers.get(_RUNNER_ID_HEADER) or "").strip()
    if not runner_uuid:
        return None

    runner = Runner.query.filter_by(runner_uuid=runner_uuid).one_or_none()
    if runner is None or not runner.enabled or runner.is_local:
        return None

    enrolled = bool(
        str(runner.pki_certificate_serial or "").strip()
        and str(runner.pki_certificate_fingerprint_sha256 or "").strip()
    )
    if enrolled:
        cert = _presented_certificate()
        if cert is None or not _certificate_matches_runner(cert, runner):
            return None
        _retire_legacy_bearer_secret(runner)
        return runner

    if not allow_legacy_unenrolled:
        return None

    authorization = str(request.headers.get("Authorization") or "")
    secret = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not secret:
        return None
    return authenticate_runner(runner_uuid, secret)
