from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class EnvironmentBuildSetting(db.Model):
    __tablename__ = "environment_build_setting"
    __table_args__ = (db.CheckConstraint("id = 1", name="ck_environment_build_setting_singleton"),)

    id = db.Column(db.Integer, primary_key=True, default=1)
    proxy_enabled = db.Column(db.Boolean, nullable=False, default=False)
    proxy_url = db.Column(db.String(1000), nullable=False, default="")
    proxy_username = db.Column(db.String(255), nullable=False, default="")
    encrypted_proxy_password = db.Column(db.LargeBinary, nullable=True)
    no_proxy = db.Column(db.Text, nullable=False, default="localhost,127.0.0.1")
    updated_by = db.Column(db.String(255), nullable=False, default="system")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def has_proxy_password(self):
        return bool(self.encrypted_proxy_password)
