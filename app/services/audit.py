"""Database-backed audit logging with defensive secret redaction."""

import json
import uuid

from flask import current_app, g, has_request_context, request

from app import db
from app.models.audit_log import AuditLog

_SENSITIVE_FRAGMENTS = (
    "password", "secret", "token", "private_key", "credential", "bind_password",
    "vault", "authorization", "cookie", "csrf",
)


def _safe_value(value, depth=0):
    if depth > 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)[:200]
            if any(fragment in key_text.casefold() for fragment in _SENSITIVE_FRAGMENTS):
                cleaned[key_text] = "[redacted]"
            else:
                cleaned[key_text] = _safe_value(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item, depth + 1) for item in list(value)[:100]]
    return str(value)[:2000]


def current_request_id():
    if not has_request_context():
        return ""
    value = getattr(g, "journeyman_request_id", None)
    if not value:
        value = uuid.uuid4().hex
        g.journeyman_request_id = value
    return value


def record_audit_event(
    action,
    *,
    result="success",
    object_type="",
    object_id="",
    object_name="",
    details=None,
    actor_username=None,
    actor_object_guid=None,
    actor_role=None,
    authenticated_via=None,
    commit=True,
):
    """Persist an audit event. Audit failures never break the user operation."""

    try:
        if has_request_context():
            actor_username = actor_username or getattr(g, "authenticated_username", None)
            actor_object_guid = actor_object_guid or getattr(g, "authenticated_user_object_guid", None)
            actor_role = actor_role or getattr(g, "authenticated_role", None)
            authenticated_via = authenticated_via or getattr(g, "authenticated_via", None)
            source_ip = str(request.remote_addr or "")[:64]
            request_id = current_request_id()
        else:
            source_ip = ""
            request_id = ""

        row = AuditLog(
            actor_username=str(actor_username or "anonymous")[:255],
            actor_object_guid=(str(actor_object_guid)[:36] if actor_object_guid else None),
            actor_role=str(actor_role or "")[:32],
            authenticated_via=str(authenticated_via or "")[:32],
            action=str(action)[:120],
            object_type=str(object_type or "")[:80],
            object_id=str(object_id or "")[:120],
            object_name=str(object_name or "")[:255],
            result=str(result or "unknown")[:32],
            source_ip=source_ip,
            request_id=request_id,
            details_json=json.dumps(_safe_value(details or {}), sort_keys=True),
        )
        db.session.add(row)
        if commit:
            db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to persist audit event %s", action)
        return None
