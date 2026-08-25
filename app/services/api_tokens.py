import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app import db
from app.models import ApiToken

TOKEN_PREFIX = "jym1_"
API_TOKEN_LIFETIME = timedelta(days=365)
API_TOKEN_EXPIRY_WARNING = timedelta(days=30)


def utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def token_expiry_warning(row, *, now=None):
    now = _as_utc(now or utcnow())
    expires_at = _as_utc(row.expires_at)
    remaining = expires_at - now
    if remaining <= timedelta(0) or remaining > API_TOKEN_EXPIRY_WARNING:
        return None
    return {
        "expires_at": expires_at,
        "remaining_days": max(0, int(remaining.total_seconds() // 86400)),
    }


def _digest(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def create_api_token(*, name, username, administrator=False):
    name = str(name or "").strip()
    username = str(username or "").strip()
    if not name or not username:
        raise ValueError("API token name and username are required.")
    if ApiToken.query.filter_by(name=name).first() is not None:
        raise ValueError('API token "{}" already exists.'.format(name))
    now = utcnow()
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        name=name,
        username=username,
        role="Administrator" if administrator else "User",
        token_digest=_digest(secret),
        enabled=True,
        created_at=now,
        expires_at=now + API_TOKEN_LIFETIME,
    )
    db.session.add(row)
    db.session.commit()
    return row, secret


def authenticate_api_token(token):
    token = str(token or "").strip()
    if not token.startswith(TOKEN_PREFIX):
        return None
    row = ApiToken.query.filter_by(token_digest=_digest(token), enabled=True).one_or_none()
    if row is None:
        return None
    now = utcnow()
    if _as_utc(row.expires_at) <= now:
        return None
    row.last_used_at = now
    db.session.commit()
    return row
