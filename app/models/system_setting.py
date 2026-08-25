from datetime import datetime, timezone

from app import db


SYSTEM_SETTING_ID = 1

APPLY_STATUS_NEVER_APPLIED = "never_applied"
APPLY_STATUS_PENDING = "pending"
APPLY_STATUS_APPLIED = "applied"
APPLY_STATUS_FAILED = "failed"

VALID_APPLY_STATUSES = {
    APPLY_STATUS_NEVER_APPLIED,
    APPLY_STATUS_PENDING,
    APPLY_STATUS_APPLIED,
    APPLY_STATUS_FAILED,
}


def utcnow():
    return datetime.now(timezone.utc)


class SystemSetting(db.Model):
    """
    Singleton containing Journeyman's desired public web settings.

    Only filesystem paths are stored. Certificate and private-key
    contents must never be stored in the database.

    A later privileged helper applies these desired settings to Nginx.
    """

    __tablename__ = "system_setting"

    __table_args__ = (
        db.CheckConstraint(
            "id = 1",
            name="ck_system_setting_singleton",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
        default=SYSTEM_SETTING_ID,
    )

    public_fqdn = db.Column(
        db.String(253),
        nullable=False,
    )

    https_port = db.Column(
        db.Integer,
        nullable=False,
        default=443,
    )

    tls_certificate_path = db.Column(
        db.String(500),
        nullable=False,
    )

    tls_private_key_path = db.Column(
        db.String(500),
        nullable=False,
    )

    tls_chain_path = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    redirect_http_to_https = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    job_retention_days = db.Column(
        db.Integer,
        nullable=False,
        default=180,
    )

    reaction_retention_days = db.Column(
        db.Integer,
        nullable=False,
        default=180,
    )

    apply_status = db.Column(
        db.String(32),
        nullable=False,
        default=APPLY_STATUS_NEVER_APPLIED,
    )

    apply_message = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    applied_config_sha256 = db.Column(
        db.String(64),
        nullable=True,
    )

    updated_by = db.Column(
        db.String(255),
        nullable=False,
        default="system",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    last_applied_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self):
        return (
            "<SystemSetting fqdn={!r} "
            "https_port={} apply_status={!r}>"
            .format(
                self.public_fqdn,
                self.https_port,
                self.apply_status,
            )
        )
