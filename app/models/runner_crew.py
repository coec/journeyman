from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


runner_crew_member = db.Table(
    "runner_crew_member",
    db.Column(
        "runner_crew_id",
        db.Integer,
        db.ForeignKey("runner_crew.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "runner_id",
        db.Integer,
        db.ForeignKey("runner.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class RunnerCrew(db.Model):
    """A logical pool of interchangeable remote execution runners."""

    __tablename__ = "runner_crew"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    projects = db.relationship(
        "Project",
        back_populates="default_runner_crew",
    )

    runners = db.relationship(
        "Runner",
        secondary=runner_crew_member,
        back_populates="crews",
        order_by="Runner.name",
    )

    def __repr__(self):
        return "<RunnerCrew {!r}>".format(self.name)
