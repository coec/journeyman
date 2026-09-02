"""Lifecycle controls for the temporary local break-glass administrator."""

from datetime import datetime, timedelta, timezone

from flask import current_app

from app import db
from app.models import AuthSession, FallbackAdminActivation
from app.services.audit import record_audit_event


FALLBACK_ADMIN_LIFETIME_SECONDS = 60 * 60
FALLBACK_ADMIN_DEFAULT_LIFETIME_MINUTES = 60
FALLBACK_ADMIN_NO_EXPIRY_AT = datetime.max.replace(tzinfo=timezone.utc)


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def fallback_admin_lifetime_minutes(lifetime_minutes=None):
    if lifetime_minutes is None:
        lifetime_minutes = current_app.config.get(
            "FALLBACK_ADMIN_LIFETIME_MINUTES",
            FALLBACK_ADMIN_DEFAULT_LIFETIME_MINUTES,
        )
    try:
        lifetime_minutes = int(lifetime_minutes)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "FALLBACK_ADMIN_LIFETIME_MINUTES must be an integer."
        ) from exc
    if lifetime_minutes < 0:
        raise RuntimeError(
            "FALLBACK_ADMIN_LIFETIME_MINUTES cannot be negative."
        )
    if lifetime_minutes > 0:
        try:
            _utc_now() + timedelta(minutes=lifetime_minutes)
        except OverflowError as exc:
            raise RuntimeError(
                "FALLBACK_ADMIN_LIFETIME_MINUTES is too large."
            ) from exc
    return lifetime_minutes


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
    """Expire the activation when its configured deadline has passed."""

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


def fallback_admin_activation_is_non_expiring(activation):
    """Return whether an activation has no automatic expiry deadline."""
    return (
        activation is not None
        and _as_utc(activation.expires_at) == FALLBACK_ADMIN_NO_EXPIRY_AT
    )


def provision_fallback_activation(*, now=None, lifetime_minutes=None):
    """Create a fresh break-glass activation using the configured lifetime."""

    now = _as_utc(now) or _utc_now()
    lifetime_minutes = fallback_admin_lifetime_minutes(lifetime_minutes)
    # Re-provisioning is a new activation, never an extension.
    expire_fallback_activation("reprovisioned", now=now)

    row = _activation()
    if row is None:
        row = FallbackAdminActivation(id=1)
        db.session.add(row)

    row.activated_at = now
    if lifetime_minutes == 0:
        row.expires_at = FALLBACK_ADMIN_NO_EXPIRY_AT
    else:
        try:
            row.expires_at = now + timedelta(minutes=lifetime_minutes)
        except OverflowError as exc:
            raise RuntimeError(
                "FALLBACK_ADMIN_LIFETIME_MINUTES is too large."
            ) from exc
    row.expired_at = None
    row.expiry_reason = ""
    db.session.commit()

    record_audit_event(
        "authentication.fallback_provisioned",
        actor_username="server-admin",
        actor_role="Server Administrator",
        authenticated_via="local-cli",
        details={
            "expires_at": (
                None
                if lifetime_minutes == 0
                else _as_utc(row.expires_at).isoformat()
            ),
            "maximum_lifetime_seconds": (
                None if lifetime_minutes == 0 else lifetime_minutes * 60
            ),
            "non_expiring": lifetime_minutes == 0,
        },
    )
    return row
