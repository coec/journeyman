from app import db


class JobRepositorySnapshot(db.Model):
    """
    Immutable snapshot of one repository used by a Job.

    A repository used by multiple JobSteps appears only once for the Job.
    """

    __tablename__ = "job_repository_snapshot"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_id = db.Column(
        db.Integer,
        db.ForeignKey("job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Reference to the live Repository record.
    repository_id = db.Column(
        db.Integer,
        db.ForeignKey("repository.id"),
        nullable=False,
    )

    # Immutable values captured when the Job is queued.
    repository_name = db.Column(
        db.String(120),
        nullable=False,
    )

    repository_url = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    repository_commit = db.Column(
        db.String(64),
        nullable=False,
    )

    repository_commit_message = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    repository_commit_author = db.Column(
        db.String(255),
        nullable=False,
        default="",
    )

    repository_commit_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    job = db.relationship(
        "Job",
        back_populates="repository_snapshots",
    )

    repository = db.relationship(
        "Repository",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "job_id",
            "repository_id",
            name="uq_job_repository_snapshot",
        ),
    )

    def __repr__(self):
        return (
            f"<JobRepositorySnapshot job_id={self.job_id} "
            f"repository_id={self.repository_id} "
            f"commit={self.repository_commit!r}>"
        )
