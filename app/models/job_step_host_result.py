from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class JobStepHostResult(db.Model):
    """Structured result for one host in a remote-shell Job step."""

    __tablename__ = "job_step_host_result"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_step_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job_step.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    host = db.Column(
        db.String(255),
        nullable=False,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
    )

    exit_code = db.Column(
        db.Integer,
        nullable=True,
    )

    runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    runner_name = db.Column(
        db.String(120),
        nullable=False,
        default="",
    )

    runner_hostname = db.Column(
        db.String(255),
        nullable=False,
        default="",
    )

    runner_local = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    stdout = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    stderr = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    step = db.relationship(
        "JobStep",
        back_populates="host_results",
    )

    runner = db.relationship("Runner")

    __table_args__ = (
        db.UniqueConstraint(
            "job_step_id",
            "host",
            name="uq_job_step_host_result_host",
        ),
    )

    def __repr__(self):
        return (
            f"<JobStepHostResult "
            f"job_step_id={self.job_step_id} "
            f"host={self.host!r} status={self.status!r}>"
        )
