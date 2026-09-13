"""Outbound HTTPS/mTLS management transport for remote runners.

Patch 6 pins each live runner peer certificate to the certificate identity
recorded at enrolment.  A CA-valid certificate is not sufficient: serial,
fingerprint and the runner URI SAN must all match the registered runner.
Identity failures quarantine the runner and prevent new work from being claimed.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import http.client
import json
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID
from flask import current_app

from app import db
from app.models import Runner
from app.services.runner_capabilities import set_reported_capabilities
from app.services.runner_environments import set_reported_runner_environments
from app.services.runner_runtime_dependencies import (
    set_reported_runner_runtime_dependencies,
)


RUNNER_IDENTITY_URI_PREFIX = "urn:journeyman:runner:"


class RunnerTransportError(RuntimeError):
    pass


class RunnerIdentityError(RunnerTransportError):
    """Raised when TLS succeeds or is attempted with an invalid runner identity."""


def _controller_tls():
    cert = str(current_app.config["RUNNER_CONTROLLER_CERTIFICATE_PATH"])
    key = str(current_app.config["RUNNER_CONTROLLER_PRIVATE_KEY_PATH"])
    ca = str(current_app.config["RUNNER_CA_CERTIFICATE_PATH"])
    missing = [path for path in (cert, key, ca) if not Path(path).is_file()]
    if missing:
        raise RunnerTransportError(
            "Runner management TLS material is missing: {}".format(", ".join(missing))
        )
    return cert, key, ca


def _management_url(hostname, port, path):
    hostname = str(hostname or "").strip()
    if not hostname:
        raise RunnerTransportError("Runner hostname is empty.")
    port = int(port or current_app.config["RUNNER_MANAGEMENT_PORT"])
    return "https://{}:{}{}".format(hostname, port, path)


def _management_transport_settings():
    """Resolve Flask-backed runner transport settings in the caller thread."""

    cert, key, ca = _controller_tls()
    return {
        "controller_cert": cert,
        "controller_key": key,
        "ca_cert": ca,
        "default_port": int(current_app.config["RUNNER_MANAGEMENT_PORT"]),
        "timeout": max(
            1, int(current_app.config["RUNNER_MANAGEMENT_TIMEOUT_SECONDS"])
        ),
    }


def _management_ssl_context(*, controller_cert, controller_key, ca_cert):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_cert)
    context.load_cert_chain(certfile=controller_cert, keyfile=controller_key)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _validate_peer_certificate(
    certificate_der,
    *,
    expected_runner_uuid,
    expected_serial,
    expected_fingerprint_sha256,
):
    """Validate the live peer against the identity issued at enrolment."""

    try:
        certificate = x509.load_der_x509_certificate(certificate_der)
    except (TypeError, ValueError) as exc:
        raise RunnerIdentityError("Runner peer certificate could not be parsed.") from exc

    actual_serial = format(certificate.serial_number, "x").lower()
    expected_serial = str(expected_serial or "").strip().lower()
    if not expected_serial or actual_serial != expected_serial:
        raise RunnerIdentityError(
            "Runner certificate serial mismatch: expected {}, received {}.".format(
                expected_serial or "<empty>", actual_serial
            )
        )

    actual_fingerprint = certificate.fingerprint(hashes.SHA256()).hex().lower()
    expected_fingerprint = str(expected_fingerprint_sha256 or "").strip().lower()
    if not expected_fingerprint or actual_fingerprint != expected_fingerprint:
        raise RunnerIdentityError(
            "Runner certificate SHA-256 fingerprint mismatch."
        )

    expected_uri = RUNNER_IDENTITY_URI_PREFIX + str(expected_runner_uuid or "").strip()
    try:
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        uri_names = san.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound as exc:
        raise RunnerIdentityError("Runner certificate has no subjectAltName extension.") from exc
    if not expected_runner_uuid or expected_uri not in uri_names:
        raise RunnerIdentityError(
            "Runner certificate URI identity mismatch: expected {}.".format(
                expected_uri or "<empty>"
            )
        )

    try:
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as exc:
        raise RunnerIdentityError("Runner certificate has no basicConstraints extension.") from exc
    if constraints.ca:
        raise RunnerIdentityError("Runner peer certificate must not be a CA certificate.")

    try:
        eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise RunnerIdentityError("Runner certificate has no extendedKeyUsage extension.") from exc
    if ExtendedKeyUsageOID.SERVER_AUTH not in eku:
        raise RunnerIdentityError("Runner certificate is not valid for TLS server authentication.")

    return certificate


def _request_runner_json(
    hostname,
    management_port,
    expected_runner_uuid,
    expected_serial,
    expected_fingerprint_sha256,
    *,
    method,
    path,
    payload=None,
    transport_settings=None,
):
    """Perform one pinned mTLS management request and return JSON.

    ``transport_settings`` may be resolved by the caller before dispatching
    work to another thread.  Flask application context is thread-local, so
    worker threads must not dereference ``current_app`` themselves.
    """

    hostname = str(hostname or "").strip()
    if not hostname:
        raise RunnerTransportError("Runner hostname is empty.")
    settings = transport_settings or _management_transport_settings()
    port = int(management_port or settings["default_port"])
    timeout = int(settings["timeout"])
    context = _management_ssl_context(
        controller_cert=settings["controller_cert"],
        controller_key=settings["controller_key"],
        ca_cert=settings["ca_cert"],
    )
    connection = http.client.HTTPSConnection(
        hostname, port, timeout=timeout, context=context
    )
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        try:
            connection.connect()
        except ssl.SSLCertVerificationError as exc:
            raise RunnerIdentityError(
                "Runner TLS certificate verification failed: {}".format(exc)
            ) from exc
        except ssl.SSLError as exc:
            raise RunnerTransportError("Runner TLS handshake failed: {}".format(exc)) from exc
        except OSError as exc:
            raise RunnerTransportError(str(exc)) from exc

        peer_der = connection.sock.getpeercert(binary_form=True) if connection.sock else None
        if not peer_der:
            raise RunnerIdentityError("Runner did not present a peer certificate.")
        _validate_peer_certificate(
            peer_der,
            expected_runner_uuid=expected_runner_uuid,
            expected_serial=expected_serial,
            expected_fingerprint_sha256=expected_fingerprint_sha256,
        )
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        if response.status < 200 or response.status >= 300:
            detail = ""
            try:
                error_payload = json.loads(response_body.decode("utf-8")) if response_body else {}
                detail = str(error_payload.get("error") or "").strip()
            except (UnicodeDecodeError, ValueError, AttributeError):
                pass
            raise RunnerTransportError(
                "Runner management request {} {} failed with HTTP {}{}.".format(
                    method, path, response.status, ": " + detail if detail else ""
                )
            )
        try:
            result = json.loads(response_body.decode("utf-8")) if response_body else {}
        except (UnicodeDecodeError, ValueError) as exc:
            raise RunnerTransportError("Runner management response was not valid JSON.") from exc
    except http.client.HTTPException as exc:
        raise RunnerTransportError(str(exc)) from exc
    finally:
        connection.close()
    if not isinstance(result, dict):
        raise RunnerTransportError("Runner management response must be a JSON object.")
    actual_uuid = str(result.get("runner_uuid") or "").strip()
    if actual_uuid and actual_uuid != str(expected_runner_uuid or "").strip():
        raise RunnerIdentityError(
            "Runner management identity mismatch: expected {}, received {}.".format(
                expected_runner_uuid, actual_uuid
            )
        )
    return result


def fetch_runner_health(
    hostname,
    management_port,
    expected_runner_uuid,
    expected_serial="",
    expected_fingerprint_sha256="",
    *,
    transport_settings=None,
):
    """Fetch one runner health document and pin its live certificate identity."""

    payload = _request_runner_json(
        hostname,
        management_port,
        expected_runner_uuid,
        expected_serial,
        expected_fingerprint_sha256,
        method="GET",
        path="/v1/health",
        transport_settings=transport_settings,
    )
    actual_uuid = str(payload.get("runner_uuid") or "").strip()
    if actual_uuid != str(expected_runner_uuid or "").strip():
        raise RunnerIdentityError(
            "Runner health identity mismatch: expected {}, received {}.".format(
                expected_runner_uuid, actual_uuid or "<empty>"
            )
        )
    return payload


def fetch_runner_renewal_csr(runner):
    payload = _request_runner_json(
        runner.hostname,
        runner.management_port,
        runner.runner_uuid,
        runner.pki_certificate_serial,
        runner.pki_certificate_fingerprint_sha256,
        method="GET",
        path="/v1/certificate/csr",
    )
    csr_pem = str(payload.get("csr_pem") or "").strip()
    if not csr_pem:
        raise RunnerTransportError("Runner renewal response did not contain a CSR.")
    return csr_pem


def install_runner_renewed_certificate(runner, *, certificate_pem, ca_certificate_pem):
    return _request_runner_json(
        runner.hostname,
        runner.management_port,
        runner.runner_uuid,
        runner.pki_certificate_serial,
        runner.pki_certificate_fingerprint_sha256,
        method="POST",
        path="/v1/certificate/install",
        payload={
            "certificate_pem": certificate_pem,
            "ca_certificate_pem": ca_certificate_pem,
        },
    )


def _apply_health(runner, payload):
    runner.hostname = str(payload.get("hostname") or runner.hostname or "")[:255]
    runner.version = str(payload.get("version") or runner.version or "")[:120]
    runner.status_message = str(payload.get("status_message") or "")[:2000]
    if isinstance(payload.get("capabilities"), list):
        runner.set_capabilities(payload["capabilities"])
    if isinstance(payload.get("managed_capabilities"), dict):
        set_reported_capabilities(runner, payload["managed_capabilities"])
    if isinstance(payload.get("environments"), list):
        set_reported_runner_environments(runner, payload["environments"])
    if isinstance(payload.get("runtime_dependencies"), dict):
        set_reported_runner_runtime_dependencies(runner, payload["runtime_dependencies"])

    try:
        runner.running_steps = max(0, int(payload.get("running_steps") or 0))
    except (TypeError, ValueError):
        pass
    for key, attribute in (
        ("load_average_1m", "load_average_1m"),
        ("load_average_5m", "load_average_5m"),
    ):
        if payload.get(key) is not None:
            try:
                setattr(runner, attribute, max(0.0, float(payload[key])))
            except (TypeError, ValueError):
                pass
    if payload.get("cpu_count") is not None:
        try:
            runner.cpu_count = max(1, int(payload["cpu_count"]))
        except (TypeError, ValueError):
            pass
    if payload.get("free_workspace_bytes") is not None:
        try:
            runner.free_workspace_bytes = max(0, int(payload["free_workspace_bytes"]))
        except (TypeError, ValueError):
            pass
    runner.last_heartbeat_at = datetime.now(timezone.utc)


def _quarantine_runner(runner, reason):
    reason = str(reason or "Runner certificate identity verification failed.")[:2000]
    if runner.pki_quarantined:
        if runner.pki_quarantine_reason != reason:
            runner.pki_quarantine_reason = reason
        return False
    runner.pki_quarantined = True
    runner.pki_quarantine_reason = reason
    runner.pki_quarantined_at = datetime.now(timezone.utc)
    runner.status_message = "PKI quarantine: {}".format(reason)[:2000]
    return True


def refresh_remote_runner_health():
    """Poll enrolled runners and quarantine certificate-identity failures."""

    rows = (
        Runner.query.filter(
            Runner.is_local.is_(False),
            Runner.enabled.is_(True),
            Runner.pki_quarantined.is_(False),
            Runner.runner_uuid.isnot(None),
            Runner.pki_certificate_fingerprint_sha256 != "",
        )
        .order_by(Runner.id)
        .all()
    )
    if not rows:
        return {"updated": 0, "failed": {}}

    targets = [
        (
            row.id,
            row.hostname,
            row.management_port,
            row.runner_uuid,
            row.pki_certificate_serial,
            row.pki_certificate_fingerprint_sha256,
        )
        for row in rows
    ]
    # Flask application context is local to this thread.  Resolve all
    # current_app-backed transport configuration before entering the pool and
    # pass only plain values to worker threads.
    transport_settings = _management_transport_settings()

    results = {}
    failures = {}
    identity_failures = {}
    workers = min(8, max(1, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(
                fetch_runner_health,
                hostname,
                management_port,
                runner_uuid,
                serial,
                fingerprint,
                transport_settings=transport_settings,
            ): runner_id
            for runner_id, hostname, management_port, runner_uuid, serial, fingerprint in targets
        }
        for future in as_completed(future_map):
            runner_id = future_map[future]
            try:
                results[runner_id] = future.result()
            except RunnerIdentityError as exc:
                failures[runner_id] = str(exc)[:1000]
                identity_failures[runner_id] = str(exc)[:2000]
            except Exception as exc:
                failures[runner_id] = str(exc)[:1000]

    changed = False
    updated = 0
    newly_quarantined = []
    for runner_id, payload in results.items():
        runner = db.session.get(Runner, runner_id)
        if runner is None:
            continue
        _apply_health(runner, payload)
        changed = True
        updated += 1
    for runner_id, reason in identity_failures.items():
        runner = db.session.get(Runner, runner_id)
        if runner is None:
            continue
        quarantined_now = _quarantine_runner(runner, reason)
        if quarantined_now:
            newly_quarantined.append(
                (runner.id, runner.name, runner.runner_uuid, reason)
            )
        changed = quarantined_now or changed
    if changed:
        db.session.commit()
    if newly_quarantined:
        from app.services.audit import record_audit_event
        for runner_id, name, runner_uuid, reason in newly_quarantined:
            record_audit_event(
                "runner.pki_quarantined",
                result="blocked",
                object_type="runner",
                object_id=str(runner_id),
                object_name=name,
                details={"reason": reason, "runner_uuid": runner_uuid},
                actor_username="system",
                authenticated_via="scheduler",
            )
    return {"updated": updated, "failed": failures}
