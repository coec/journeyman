"""Database-backed rate limits for resource-intensive operations."""

from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, request

from app import db
from app.models import AuditLog


_POLICIES = {
    "execution_preview": ("COSTLY_PREVIEW_USER_LIMIT", "COSTLY_PREVIEW_GLOBAL_LIMIT"),
    "execution_launch": ("COSTLY_LAUNCH_USER_LIMIT", "COSTLY_LAUNCH_GLOBAL_LIMIT"),
    "inventory_refresh": ("COSTLY_INVENTORY_USER_LIMIT", "COSTLY_INVENTORY_GLOBAL_LIMIT"),
    "repository_sync": ("COSTLY_REPOSITORY_USER_LIMIT", "COSTLY_REPOSITORY_GLOBAL_LIMIT"),
    "environment_build": ("COSTLY_ENVIRONMENT_USER_LIMIT", "COSTLY_ENVIRONMENT_GLOBAL_LIMIT"),
}


def _username():
    return str(getattr(g, "authenticated_username", None) or "anonymous")[:255]


def _limits(operation):
    try:
        user_key, global_key = _POLICIES[operation]
    except KeyError as exc:
        raise ValueError("Unknown costly operation {!r}".format(operation)) from exc

    return (
        max(1, int(current_app.config[user_key])),
        max(1, int(current_app.config[global_key])),
        max(1, int(current_app.config["COSTLY_OPERATION_WINDOW_SECONDS"])),
    )


def _attempt_query(operation, cutoff):
    return AuditLog.query.filter(
        AuditLog.action == "security.costly_operation_attempt",
        AuditLog.object_type == "costly_operation",
        AuditLog.object_name == operation,
        AuditLog.occurred_at >= cutoff,
    )


def check_and_record_costly_operation(operation, now=None):
    """Consume one operation attempt or return a 429 response.

    AuditLog is intentionally used as the shared backing store so limits apply
    across Gunicorn workers rather than existing only in one Python process.
    Attempts are committed before the expensive work starts, so failures also
    consume budget and cannot be used to bypass the limiter.
    """

    now = now or datetime.now(timezone.utc)
    user_limit, global_limit, window_seconds = _limits(operation)
    cutoff = now - timedelta(seconds=window_seconds)
    username = _username()

    query = _attempt_query(operation, cutoff)
    global_count = query.count()
    user_count = query.filter(AuditLog.actor_username == username).count()

    if global_count >= global_limit or user_count >= user_limit:
        current_app.logger.warning(
            "Costly operation rate limit exceeded: operation=%s user=%s",
            operation,
            username,
        )
        response = jsonify(
            {
                "error": "Too many resource-intensive requests. Please try again later.",
                "operation": operation,
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(window_seconds)
        return response

    row = AuditLog(
        occurred_at=now,
        actor_username=username,
        actor_object_guid=(
            str(getattr(g, "authenticated_user_object_guid", "") or "")[:36] or None
        ),
        actor_role=str(getattr(g, "authenticated_role", "") or "")[:32],
        authenticated_via=str(getattr(g, "authenticated_via", "") or "")[:32],
        action="security.costly_operation_attempt",
        object_type="costly_operation",
        object_name=operation,
        result="attempt",
        source_ip=str(request.remote_addr or "")[:64],
        request_id=str(getattr(g, "request_id", "") or "")[:64],
        details_json="{}",
    )
    try:
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Unable to persist costly-operation rate-limit state"
        )
        response = jsonify(
            {"error": "Unable to verify resource-operation rate limit."}
        )
        response.status_code = 503
        return response

    return None


def costly_operation_rate_limit(operation):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            limited = check_and_record_costly_operation(operation)
            if limited is not None:
                return limited
            return view(*args, **kwargs)

        return wrapped

    return decorator
