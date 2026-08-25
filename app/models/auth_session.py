from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class AuthSession(db.Model):
    """Server-side revocation record for an authenticated browser session."""

    __tablename__ = "auth_session"

    session_id = db.Column(db.String(64), primary_key=True)
    username = db.Column(db.String(255), nullable=False, index=True)
    user_object_guid = db.Column(db.String(36), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    last_seen_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    directory_checked_at = db.Column(
        db.DateTime(timezone=True), nullable=True, index=True
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    @property
    def revoked(self):
        return self.revoked_at is not None
