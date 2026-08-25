from datetime import datetime, timezone

from app import db
from app.credential_crypto import decrypt_credential_data, encrypt_credential_data_with_key_id


def utcnow():
    return datetime.now(timezone.utc)


CHANNEL_EMAIL = "email"
CHANNEL_WEBHOOK = "webhook"
CHANNEL_SYSLOG = "syslog"
VALID_NOTIFICATION_CHANNELS = {CHANNEL_EMAIL, CHANNEL_WEBHOOK, CHANNEL_SYSLOG}


class NotificationTarget(db.Model):
    __tablename__ = "notification_target"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(500), nullable=False, default="")
    channel = db.Column(db.String(32), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    host = db.Column(db.String(255), nullable=False, default="")
    port = db.Column(db.Integer, nullable=False, default=0)
    tls_mode = db.Column(db.String(16), nullable=False, default="starttls")
    username = db.Column(db.String(255), nullable=False, default="")
    sender = db.Column(db.String(500), nullable=False, default="")
    recipients = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.String(2000), nullable=False, default="")
    syslog_protocol = db.Column(db.String(8), nullable=False, default="udp")

    encrypted_secret = db.Column(db.LargeBinary, nullable=True)
    secret_key_id = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    rules = db.relationship("NotificationRule", back_populates="target", passive_deletes=True)
    deliveries = db.relationship("NotificationDelivery", back_populates="target", passive_deletes=True)

    def set_secret(self, value):
        value = str(value or "")
        if not value:
            self.encrypted_secret = None
            self.secret_key_id = None
            return
        encrypted, key_id = encrypt_credential_data_with_key_id({"secret": value})
        self.encrypted_secret = encrypted
        self.secret_key_id = key_id

    def get_secret(self):
        if not self.encrypted_secret:
            return ""
        return str(decrypt_credential_data(self.encrypted_secret, self.secret_key_id).get("secret") or "")


class NotificationRule(db.Model):
    __tablename__ = "notification_rule"
    __table_args__ = (
        db.UniqueConstraint("scope_type", "scope_id", "event_type", "target_id", name="uq_notification_rule_scope_event_target"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String(32), nullable=False, index=True)
    scope_id = db.Column(db.Integer, nullable=False, index=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    target_id = db.Column(db.Integer, db.ForeignKey("notification_target.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    target = db.relationship("NotificationTarget", back_populates="rules")


class NotificationEvent(db.Model):
    __tablename__ = "notification_event"
    __table_args__ = (
        db.UniqueConstraint("event_type", "job_id", "step_id", "event_key", name="uq_notification_event_identity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    event_key = db.Column(db.String(255), nullable=False, default="")
    job_id = db.Column(db.Integer, db.ForeignKey("job.id", ondelete="CASCADE"), nullable=True, index=True)
    step_id = db.Column(db.Integer, db.ForeignKey("job_step.id", ondelete="CASCADE"), nullable=True, index=True)
    reaction_id = db.Column(db.Integer, db.ForeignKey("reaction.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    snapshot_json = db.Column(db.Text, nullable=False, default="")

    job = db.relationship("Job")
    step = db.relationship("JobStep")
    reaction = db.relationship("Reaction")
    deliveries = db.relationship("NotificationDelivery", back_populates="event", cascade="all, delete-orphan")


class NotificationDelivery(db.Model):
    __tablename__ = "notification_delivery"
    __table_args__ = (
        db.UniqueConstraint("event_id", "target_id", name="uq_notification_delivery_event_target"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("notification_event.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = db.Column(db.Integer, db.ForeignKey("notification_target.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)

    event = db.relationship("NotificationEvent", back_populates="deliveries")
    target = db.relationship("NotificationTarget", back_populates="deliveries")
