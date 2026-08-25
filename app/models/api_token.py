from datetime import datetime, timedelta, timezone

from app import db


API_TOKEN_LIFETIME_DAYS = 365


def utcnow():
    return datetime.now(timezone.utc)


def default_expiry():
    return utcnow() + timedelta(days=API_TOKEN_LIFETIME_DAYS)


class ApiToken(db.Model):
    __tablename__ = "api_token"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    username = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(32), nullable=False, default="User")
    token_digest = db.Column(db.String(64), nullable=False, unique=True, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, default=default_expiry, index=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
