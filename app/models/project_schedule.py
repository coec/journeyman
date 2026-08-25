"""Project schedule persistence."""

from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class ProjectSchedule(db.Model):
    __tablename__ = "project_schedule"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
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
    claimed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_by = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project = db.relationship("Project", back_populates="schedules")
    last_job = db.relationship("Job", foreign_keys=[last_job_id])

    __table_args__ = (
        db.UniqueConstraint("project_id", "name", name="uq_project_schedule_project_name"),
    )
