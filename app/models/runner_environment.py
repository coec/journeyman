from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class RunnerEnvironment(db.Model):
    """One execution environment reported by one registered runner."""

    __tablename__ = "runner_environment"

    id = db.Column(db.Integer, primary_key=True)
    runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    environment_id = db.Column(
        db.Integer,
        db.ForeignKey("environment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, default="not_installed")
    environment_revision = db.Column(db.String(64), nullable=False, default="")
    local_path = db.Column(db.String(1000), nullable=False, default="")
    message = db.Column(db.Text, nullable=False, default="")
    last_reported_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    runner = db.relationship("Runner", back_populates="environment_states")
    environment = db.relationship("Environment", back_populates="runner_states")

    __table_args__ = (
        db.UniqueConstraint(
            "runner_id",
            "environment_id",
            name="uq_runner_environment_runner_environment",
        ),
    )

    def __repr__(self):
        return (
            f"<RunnerEnvironment runner_id={self.runner_id} "
            f"environment_id={self.environment_id} status={self.status!r}>"
        )
