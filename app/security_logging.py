"""Central security logging helpers and log-injection protection."""

import logging

from flask import current_app, has_request_context, request

from app.services.audit import record_audit_event


def _escape_log_text(value):
    """Make untrusted text safe for line-oriented application logs."""
    if not isinstance(value, str):
        return value
    return value.replace("\r", r"\r").replace("\n", r"\n")


def _escape_log_value(value):
    if isinstance(value, str):
        return _escape_log_text(value)
    if isinstance(value, tuple):
        return tuple(_escape_log_value(item) for item in value)
    if isinstance(value, list):
        return [_escape_log_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _escape_log_value(key): _escape_log_value(item)
            for key, item in value.items()
        }
    return value


class LogInjectionFilter(logging.Filter):
    """Escape CR/LF in message templates and arguments before formatting."""

    def filter(self, record):
        record.msg = _escape_log_text(record.msg)
        record.args = _escape_log_value(record.args)
        return True


def install_log_injection_filters(app):
    """Install the CR/LF filter on every handler used by the app logger."""
    for handler in app.logger.handlers:
        if not any(isinstance(item, LogInjectionFilter) for item in handler.filters):
            handler.addFilter(LogInjectionFilter())


def record_security_rejection(category, *, status_code, reason="", details=None):
    """Record a fail-closed security/control rejection without secret data."""
    payload = {
        "category": str(category or "security_control"),
        "status_code": int(status_code),
        "reason": str(reason or "")[:240],
    }
    if has_request_context():
        payload.update({
            "method": request.method,
            "path": request.path,
        })
    if details:
        payload["context"] = details

    return record_audit_event(
        "security.control_rejected",
        result="failure",
        details=payload,
    )


def audit_rejected_response(response):
    """Audit generic validation/business-logic rejections not otherwise fatal."""
    if response.status_code in {400, 409, 422, 429}:
        # Authentication, CSRF, and explicit anti-automation paths may also
        # create more specific audit events. This generic event ensures the
        # rejection itself is still represented consistently.
        record_security_rejection(
            "request_rejected",
            status_code=response.status_code,
            reason="application_rejected_request",
        )
    return response
