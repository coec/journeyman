from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class FallbackAdminActivation(db.Model):
    """Singleton lifecycle record for the local break-glass administrator."""

    __tablename__ = "fallback_admin_activation"

    id = db.Column(db.Integer(), primary_key=True)
    activated_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    expired_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    expiry_reason = db.Column(db.String(32), nullable=False, default="")

    @property
    def active(self):
        return self.expired_at is None
