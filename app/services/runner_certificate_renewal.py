"""Automatic lifecycle management for runner management certificates."""

from datetime import datetime, timedelta, timezone

from flask import current_app

from app import db
from app.models import Runner
from app.services.audit import record_audit_event
from app.services.runner_pki import (
    controller_client_identity_status,
    ensure_controller_client_identity,
    renew_controller_client_identity,
    renew_runner_ca_certificate,
    runner_ca_status,
    sign_runner_csr,
)
from app.services.runner_transport import (
    fetch_runner_renewal_csr,
    install_runner_renewed_certificate,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _audit(action, *, runner=None, result="success", details=None):
    record_audit_event(
        action,
        result=result,
        object_type="runner" if runner is not None else "runner_pki",
        object_id=str(runner.id) if runner is not None else None,
        object_name=runner.name if runner is not None else "Runner PKI",
        details=details or {},
        actor_username="system",
        authenticated_via="scheduler",
    )


def _runner_renewal_due(runner, *, now):
    not_after = _as_utc(runner.pki_certificate_not_after_at)
    if not_after is None:
        return False
    renew_before = timedelta(
        days=int(current_app.config["RUNNER_CERTIFICATE_RENEW_BEFORE_DAYS"])
    )
    return now >= not_after - renew_before


def _runner_retry_due(runner, *, now):
    last_attempt = _as_utc(runner.pki_renewal_last_attempt_at)
    if last_attempt is None:
        return True
    retry = timedelta(
        seconds=max(3600, int(current_app.config["RUNNER_CERTIFICATE_RETRY_SECONDS"]))
    )
    return now >= last_attempt + retry


def _renew_one_runner(runner, *, now):
    """Renew one runner using its existing private key and UUID."""

    runner.pki_renewal_last_attempt_at = now
    db.session.commit()
    try:
        csr_pem = fetch_runner_renewal_csr(runner)
        issued = sign_runner_csr(
            csr_pem,
            runner_uuid=runner.runner_uuid,
            hostname=runner.hostname,
            now=now,
        )
        installed = install_runner_renewed_certificate(
            runner,
            certificate_pem=issued["certificate_pem"],
            ca_certificate_pem=issued["ca_certificate_pem"],
        )
        if str(installed.get("serial") or "").lower() != issued["serial"].lower():
            raise RuntimeError("Runner reported an unexpected installed certificate serial.")
        if (
            str(installed.get("fingerprint_sha256") or "").lower()
            != issued["fingerprint_sha256"].lower()
        ):
            raise RuntimeError("Runner reported an unexpected installed certificate fingerprint.")
    except Exception as exc:
        runner = db.session.get(Runner, runner.id)
        runner.pki_renewal_failure_count = int(runner.pki_renewal_failure_count or 0) + 1
        runner.pki_renewal_last_error = str(exc)[:2000]
        warning_threshold = max(
            1, int(current_app.config["RUNNER_CERTIFICATE_WARNING_FAILURES"])
        )
        warning_now = (
            runner.pki_renewal_failure_count >= warning_threshold
            and runner.pki_renewal_warning_at is None
        )
        if warning_now:
            runner.pki_renewal_warning_at = now
        db.session.commit()
        _audit(
            "runner.pki_certificate_renewal_failed",
            runner=runner,
            result="failed",
            details={
                "failure_count": runner.pki_renewal_failure_count,
                "error": runner.pki_renewal_last_error,
            },
        )
        if warning_now:
            _audit(
                "runner.pki_certificate_renewal_warning",
                runner=runner,
                result="warning",
                details={"failure_count": runner.pki_renewal_failure_count},
            )
        return False, runner.pki_renewal_last_error

    runner = db.session.get(Runner, runner.id)
    runner.pki_certificate_serial = issued["serial"]
    runner.pki_certificate_fingerprint_sha256 = issued["fingerprint_sha256"]
    runner.pki_certificate_not_before_at = issued["not_before"]
    runner.pki_certificate_not_after_at = issued["not_after"]
    runner.pki_certificate_issued_at = now
    runner.pki_renewal_failure_count = 0
    runner.pki_renewal_last_error = ""
    runner.pki_renewal_warning_at = None
    db.session.commit()
    _audit(
        "runner.pki_certificate_renewed",
        runner=runner,
        details={
            "serial": issued["serial"],
            "fingerprint_sha256": issued["fingerprint_sha256"],
            "not_after": issued["not_after"].isoformat(),
        },
    )
    return True, ""


def maintain_runner_certificates(*, now=None):
    """Renew CA/controller identities and due runner certificates.

    The scheduler may call this frequently; persisted runner attempt timestamps
    enforce the one-day retry cadence after a failed renewal.
    """

    now = _as_utc(now or _utcnow())
    result = {
        "ca_renewed": False,
        "controller_renewed": False,
        "runner_renewed": [],
        "runner_failed": {},
    }

    ca = runner_ca_status(now=now)
    if ca.get("initialized") and ca.get("renewal_due"):
        metadata = renew_runner_ca_certificate(now=now)
        result["ca_renewed"] = True
        _audit(
            "runner_pki.ca_auto_renewed",
            details={"not_after": metadata["not_after"]},
        )

    controller = controller_client_identity_status(now=now)
    if not controller.get("initialized"):
        ensure_controller_client_identity(now=now)
        result["controller_renewed"] = True
        _audit("runner_pki.controller_identity_created")
    elif controller.get("renewal_due"):
        renewed = renew_controller_client_identity(now=now)
        result["controller_renewed"] = True
        _audit(
            "runner_pki.controller_certificate_renewed",
            details={"not_after": renewed["not_after"].isoformat()},
        )

    runners = (
        Runner.query.filter(
            Runner.is_local.is_(False),
            Runner.enabled.is_(True),
            Runner.pki_quarantined.is_(False),
            Runner.runner_uuid.isnot(None),
            Runner.pki_certificate_fingerprint_sha256 != "",
            Runner.pki_certificate_not_after_at.isnot(None),
        )
        .order_by(Runner.id)
        .all()
    )
    for runner in runners:
        if not _runner_renewal_due(runner, now=now):
            continue
        if not _runner_retry_due(runner, now=now):
            continue
        ok, error = _renew_one_runner(runner, now=now)
        if ok:
            result["runner_renewed"].append(runner.id)
        else:
            result["runner_failed"][runner.id] = error
    return result
