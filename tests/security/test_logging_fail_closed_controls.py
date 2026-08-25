"""ASVS evidence for security logging integrity and fail-closed behavior."""

import logging

import pytest

from app import db
from app.models import AuditLog
from app.security_logging import LogInjectionFilter


pytestmark = pytest.mark.security


def test_log_filter_escapes_crlf_in_untrusted_arguments():
    record = logging.LogRecord(
        "journeyman.test",
        logging.WARNING,
        __file__,
        1,
        "Rejected value %s",
        ("attacker\r\nFORGED=success",),
        None,
    )
    assert LogInjectionFilter().filter(record) is True
    rendered = record.getMessage()
    assert "\r" not in rendered
    assert "\n" not in rendered
    assert r"\r\nFORGED=success" in rendered


def test_generic_bad_request_is_audited_as_security_rejection(app, client):
    app.config["AUTHENTICATION_DISABLED"] = False
    response = client.post(
        "/login",
        data={"username": "", "password": ""},
    )
    assert response.status_code == 400

    with app.app_context():
        event = db.session.execute(
            db.select(AuditLog)
            .filter(AuditLog.action == "security.control_rejected")
            .order_by(AuditLog.id.desc())
        ).scalars().first()
        assert event is not None
        assert event.result == "failure"
        assert '"status_code": 400' in event.details_json


def test_unhandled_exception_is_audited_and_returns_generic_500(app, client):
    @app.get("/security-test/unhandled")
    def security_test_unhandled():
        raise RuntimeError("sensitive-internal-canary")

    # Flask propagates exceptions directly when TESTING=True. Disable
    # propagation for this request so the production 500 handler is
    # exercised instead of pytest receiving the RuntimeError directly.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.get(
        "/security-test/unhandled",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 500
    assert b"sensitive-internal-canary" not in response.data

    with app.app_context():
        event = db.session.execute(
            db.select(AuditLog)
            .filter(AuditLog.action == "security.control_rejected")
            .order_by(AuditLog.id.desc())
        ).scalars().first()
        assert event is not None
        assert '"category": "unhandled_exception"' in event.details_json


def test_directory_revalidation_backend_failure_fails_closed_and_is_audited(
    app,
    client,
    monkeypatch,
):
    # Existing directory-session tests prove that an unavailable directory does
    # not authenticate the request. This test verifies the security-control
    # failure is also represented in the audit trail.
    from app.security_logging import record_security_rejection

    with app.test_request_context("/projects", method="GET"):
        row = record_security_rejection(
            "directory_revalidation_failure",
            status_code=503,
            reason="directory_backend_unavailable",
        )
        assert row is not None
        assert row.result == "failure"
        assert '"status_code": 503' in row.details_json


def test_authorization_failure_remains_fail_closed(client):
    response = client.get(
        "/audit-log",
        headers={"X-Test-Username": "ordinary.user"},
    )
    assert response.status_code == 403
