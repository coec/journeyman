from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class AuditLog(db.Model):
    """Immutable security and administrative audit event."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    actor_username = db.Column(db.String(255), nullable=False, default="system", index=True)
    actor_object_guid = db.Column(db.String(36), nullable=True, index=True)
    actor_role = db.Column(db.String(32), nullable=False, default="")
    authenticated_via = db.Column(db.String(32), nullable=False, default="")
    action = db.Column(db.String(120), nullable=False, index=True)
    object_type = db.Column(db.String(80), nullable=False, default="", index=True)
    object_id = db.Column(db.String(120), nullable=False, default="")
    object_name = db.Column(db.String(255), nullable=False, default="")
    result = db.Column(db.String(32), nullable=False, default="success", index=True)
    source_ip = db.Column(db.String(64), nullable=False, default="")
    request_id = db.Column(db.String(64), nullable=False, default="", index=True)
    details_json = db.Column(db.Text, nullable=False, default="{}")

    def __repr__(self):
        return "<AuditLog id={} action={!r} result={!r}>".format(
            self.id, self.action, self.result
        )
