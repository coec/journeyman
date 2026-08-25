from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class ReleaseTestSetting(db.Model):
    __tablename__ = "release_test_setting"
    __table_args__ = (
        db.CheckConstraint("id = 1", name="ck_release_test_setting_singleton"),
    )

    id = db.Column(db.Integer, primary_key=True, default=1)
    inventory_id = db.Column(
        db.Integer,
        db.ForeignKey("inventory_source.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    credential_id = db.Column(
        db.Integer,
        db.ForeignKey("credential.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    runner_crew_id = db.Column(
        db.Integer,
        db.ForeignKey("runner_crew.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    host_pattern = db.Column(db.String(500), nullable=False, default="")
    alternate_become_users = db.Column(db.Text, nullable=False, default="")
    updated_by = db.Column(db.String(255), nullable=False, default="system")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    inventory = db.relationship("Inventory", foreign_keys=[inventory_id])
    credential = db.relationship("Credential", foreign_keys=[credential_id])
    runner_crew = db.relationship("RunnerCrew", foreign_keys=[runner_crew_id])

    def become_users(self):
        return [
            item.strip()
            for item in (self.alternate_become_users or "").splitlines()
            if item.strip()
        ]
