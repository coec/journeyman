"""Project schedule persistence."""

from datetime import datetime, timezone

from app import db
from app.credential_crypto import decrypt_credential_data, encrypt_credential_data_with_key_id


def utcnow():
    return datetime.now(timezone.utc)


class ProjectSchedule(db.Model):
    __tablename__ = "project_schedule"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    package_id = db.Column(
        db.Integer,
        db.ForeignKey("project_package.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    schedule_type = db.Column(db.String(20), nullable=False, default="once")
    timezone_name = db.Column(db.String(64), nullable=False, default="UTC")
    start_at = db.Column(db.DateTime(timezone=True), nullable=False)
    end_at = db.Column(db.DateTime(timezone=True), nullable=True)
    interval_minutes = db.Column(db.Integer, nullable=True)
    weekdays = db.Column(db.String(32), nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    next_run_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    last_run_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_job_id = db.Column(db.Integer, db.ForeignKey("job.id", ondelete="SET NULL"), nullable=True)
    last_error = db.Column(db.Text, nullable=False, default="")
    encrypted_package_answers = db.Column(db.LargeBinary, nullable=True)
    package_answers_key_id = db.Column(db.String(120), nullable=True)
    claimed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_by = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project = db.relationship("Project", back_populates="schedules")
    package = db.relationship("ProjectPackage", back_populates="schedules")
    last_job = db.relationship("Job", foreign_keys=[last_job_id])


    def set_package_answers(self, answers):
        if not answers:
            self.encrypted_package_answers = None
            self.package_answers_key_id = None
            return
        encrypted, key_id = encrypt_credential_data_with_key_id({"answers": dict(answers)})
        self.encrypted_package_answers = encrypted
        self.package_answers_key_id = key_id

    def get_package_answers(self):
        if not self.encrypted_package_answers:
            return {}
        payload = decrypt_credential_data(
            self.encrypted_package_answers,
            self.package_answers_key_id,
        )
        answers = payload.get("answers") or {}
        return answers if isinstance(answers, dict) else {}

    @property
    def target_project(self):
        return self.package.project if self.package is not None else self.project

    @property
    def target_name(self):
        return self.package.name if self.package is not None else (self.project.name if self.project is not None else "")

    @property
    def target_type(self):
        return "package" if self.package_id is not None else "project"

    __table_args__ = (
        db.UniqueConstraint("project_id", "name", name="uq_project_schedule_project_name"),
        db.UniqueConstraint("package_id", "name", name="uq_project_schedule_package_name"),
        db.CheckConstraint(
            "(project_id IS NOT NULL AND package_id IS NULL) OR "
            "(project_id IS NULL AND package_id IS NOT NULL)",
            name="ck_project_schedule_one_target",
        ),
    )
