"""Lifecycle controls for the temporary local break-glass administrator."""

from datetime import datetime, timedelta, timezone

from flask import current_app

from app import db
from app.models import AuthSession, FallbackAdminActivation
from app.services.audit import record_audit_event


FALLBACK_ADMIN_LIFETIME_SECONDS = 60 * 60


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fallback_username():
    return str(current_app.config["FALLBACK_ADMIN_USERNAME"]).strip()


def _activation():
    return db.session.get(FallbackAdminActivation, 1)


def _revoke_fallback_sessions(now):
    username = _fallback_username()
    rows = (
        AuthSession.query
        .filter(AuthSession.revoked_at.is_(None))
        .filter(db.func.lower(AuthSession.username) == username.casefold())
        .all()
    )
    for row in rows:
        row.revoked_at = now
    return len(rows)


def expire_fallback_activation(reason, *, now=None):
    """Expire the current activation and revoke every fallback browser session."""

    now = _as_utc(now) or _utc_now()
    row = _activation()
    changed = False

    if row is not None and row.expired_at is None:
        row.expired_at = now
        row.expiry_reason = str(reason or "expired")[:32]
        changed = True

    revoked = _revoke_fallback_sessions(now)

    if changed or revoked:
        db.session.commit()
        record_audit_event(
            "authentication.fallback_expired",
            actor_username=_fallback_username(),
            actor_role="Administrator",
            authenticated_via="fallback",
            details={
                "reason": str(reason or "expired"),
                "sessions_revoked": revoked,
            },
        )
        return True

    return False


def expire_fallback_activation_if_due(*, now=None):
    """Expire the activation when its immutable 60-minute deadline has passed."""

    now = _as_utc(now) or _utc_now()
    row = _activation()
    if row is None or row.expired_at is not None:
        return False
    if _as_utc(row.expires_at) > now:
        return False
    return expire_fallback_activation("timeout", now=now)


def active_fallback_activation(*, now=None):
    """Return the active activation, failing closed after its deadline."""

    now = _as_utc(now) or _utc_now()
    row = _activation()
    if row is None or row.expired_at is not None:
        return None
    if _as_utc(row.expires_at) <= now:
        expire_fallback_activation("timeout", now=now)
        return None
    return row


def provision_fallback_activation(*, now=None):
    """Create a fresh, non-renewable 60-minute activation."""

    now = _as_utc(now) or _utc_now()

    # Re-provisioning is a new activation, never an extension.
    expire_fallback_activation("reprovisioned", now=now)

    row = _activation()
    if row is None:
        row = FallbackAdminActivation(id=1)
        db.session.add(row)

    row.activated_at = now
    row.expires_at = now + timedelta(seconds=FALLBACK_ADMIN_LIFETIME_SECONDS)
    row.expired_at = None
    row.expiry_reason = ""
    db.session.commit()

    record_audit_event(
        "authentication.fallback_provisioned",
        actor_username="server-admin",
        actor_role="Server Administrator",
        authenticated_via="local-cli",
        details={
            "expires_at": _as_utc(row.expires_at).isoformat(),
            "maximum_lifetime_seconds": FALLBACK_ADMIN_LIFETIME_SECONDS,
        },
    )
    return row
