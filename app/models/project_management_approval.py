from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app import db


def utcnow():
    return datetime.now(timezone.utc)


PROJECT_MANAGEMENT_APPROVAL_PENDING = "pending"
PROJECT_MANAGEMENT_APPROVAL_APPROVED = "approved"
PROJECT_MANAGEMENT_APPROVAL_REJECTED = "rejected"
PROJECT_MANAGEMENT_APPROVAL_STALE = "stale"

VALID_PROJECT_MANAGEMENT_APPROVAL_STATUSES = {
    PROJECT_MANAGEMENT_APPROVAL_PENDING,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_REJECTED,
    PROJECT_MANAGEMENT_APPROVAL_STALE,
}


class ProjectManagementApproval(db.Model):
    """Management approval bound to one technically reviewed revision."""

    __tablename__ = "project_management_approval"

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

    technical_review_id = db.Column(
        db.Integer,
        db.ForeignKey("project_review.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    requested_by = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    approver_username = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default=PROJECT_MANAGEMENT_APPROVAL_PENDING,
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
        back_populates="management_approvals",
    )
    revision = db.relationship("ProjectRevision")
    technical_review = db.relationship("ProjectReview")

    @validates("status")
    def _validate_status(self, _key, value):
        value = str(value or "").strip()
        if value not in VALID_PROJECT_MANAGEMENT_APPROVAL_STATUSES:
            raise ValueError(
                "Invalid Project management approval status: {}".format(
                    value
                )
            )
        return value

    def __repr__(self):
        return (
            "<ProjectManagementApproval project_id={} revision_id={} "
            "approver={!r} status={!r}>"
        ).format(
            self.project_id,
            self.revision_id,
            self.approver_username,
            self.status,
        )
