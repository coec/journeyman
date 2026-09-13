from datetime import datetime, timezone

from sqlalchemy import event

from app import db
from .job import WorkItem


def utcnow():
    return datetime.now(timezone.utc)


class RunnerEnvironmentSync(db.Model):
    """One requested synchronization of an Environment to a remote runner."""

    __tablename__ = "runner_environment_sync"

    id = db.Column(db.Integer, primary_key=True)
    runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_item.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    environment_id = db.Column(
        db.Integer,
        db.ForeignKey("environment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_revision = db.Column(db.String(64), nullable=False, default="")
    requested_by = db.Column(
        db.String(255), nullable=False, default="system", index=True
    )
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    message = db.Column(db.Text, nullable=False, default="")
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    work_item = db.relationship("WorkItem")
    runner = db.relationship("Runner", back_populates="environment_syncs")
    environment = db.relationship("Environment", back_populates="runner_syncs")

    __table_args__ = (
        db.UniqueConstraint(
            "runner_id",
            "environment_id",
            name="uq_runner_environment_sync_runner_environment",
        ),
    )

    def __repr__(self):
        return (
            f"<RunnerEnvironmentSync runner_id={self.runner_id} "
            f"environment_id={self.environment_id} status={self.status!r}>"
        )


@event.listens_for(RunnerEnvironmentSync, "before_insert")
def _allocate_environment_sync_work_item(_mapper, connection, target):
    """Give directly-created sync rows a number from the shared work sequence."""

    if target.work_item_id is not None:
        return
    result = connection.execute(
        WorkItem.__table__.insert().values(kind="environment_sync")
    )
    target.work_item_id = result.inserted_primary_key[0]
