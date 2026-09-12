from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app import db


def utcnow():
    return datetime.now(timezone.utc)


PROJECT_REVIEW_PENDING = "pending"
PROJECT_REVIEW_APPROVED = "approved"
PROJECT_REVIEW_REJECTED = "rejected"
PROJECT_REVIEW_STALE = "stale"

VALID_PROJECT_REVIEW_STATUSES = {
    PROJECT_REVIEW_PENDING,
    PROJECT_REVIEW_APPROVED,
    PROJECT_REVIEW_REJECTED,
    PROJECT_REVIEW_STALE,
}


class ProjectReview(db.Model):
    """Technical-review attempt bound to one immutable Project revision."""

    __tablename__ = "project_review"

    id = db.Column(db.Integer, primary_key=True)

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    revision_id = db.Column(
        db.Integer,
        db.ForeignKey("project_revision.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    requested_by = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    reviewer_username = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default=PROJECT_REVIEW_PENDING,
        index=True,
    )

    requested_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    decided_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    decision_comment = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    project = db.relationship(
        "Project",
        back_populates="reviews",
    )

    revision = db.relationship("ProjectRevision")

    @validates("status")
    def _validate_status(self, _key, value):
        value = str(value or "").strip()
        if value not in VALID_PROJECT_REVIEW_STATUSES:
            raise ValueError(
                "Invalid Project review status: {}".format(value)
            )
        return value

    def __repr__(self):
        return (
            "<ProjectReview project_id={} revision_id={} "
            "reviewer={!r} status={!r}>"
        ).format(
            self.project_id,
            self.revision_id,
            self.reviewer_username,
            self.status,
        )
