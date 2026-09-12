from datetime import datetime, timezone
import json

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class ProjectRevision(db.Model):
    """Immutable snapshot of a Project's executable definition."""

    __tablename__ = "project_revision"

    id = db.Column(db.Integer, primary_key=True)

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sequence = db.Column(db.Integer, nullable=False)

    digest = db.Column(
        db.String(64),
        nullable=False,
        index=True,
    )

    snapshot_json = db.Column(db.Text, nullable=False)

    created_by = db.Column(
        db.String(255),
        nullable=False,
        default="system",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    project = db.relationship(
        "Project",
        back_populates="revisions",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "sequence",
            name="uq_project_revision_sequence",
        ),
    )

    def snapshot(self):
        try:
            value = json.loads(self.snapshot_json or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def __repr__(self):
        return (
            f"<ProjectRevision project_id={self.project_id} "
            f"sequence={self.sequence} digest={self.digest!r}>"
        )
