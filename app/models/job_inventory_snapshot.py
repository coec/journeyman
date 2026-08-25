from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class JobInventorySnapshot(db.Model):
    """
    Immutable resolved inventory used by one Job.

    The canonical inventory JSON is stored on disk. This database row
    records its identity, location and integrity checksum.
    """

    __tablename__ = "job_inventory_snapshot"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    inventory_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "inventory_source.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    inventory_name = db.Column(
        db.String(120),
        nullable=False,
    )

    inventory_type = db.Column(
        db.String(40),
        nullable=False,
    )

    version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    host_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    content_path = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    content_sha256 = db.Column(
        db.String(64),
        nullable=False,
        default="",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    job = db.relationship(
        "Job",
        back_populates="inventory_snapshots",
    )

    steps = db.relationship(
        "JobStep",
        back_populates="inventory_snapshot",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "job_id",
            "version",
            name="uq_job_inventory_snapshot_version",
        ),
    )

    def __repr__(self):
        return (
            f"<JobInventorySnapshot "
            f"job_id={self.job_id} "
            f"version={self.version}>"
        )
