from datetime import datetime, timezone
from app.security_scope import SECURITY_SCOPE_PRIVATE

from app import db


project_credential = db.Table(
    "project_credential",
    db.Column(
        "project_id",
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "credential_id",
        db.Integer,
        db.ForeignKey("credential.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


def utcnow():
    return datetime.now(timezone.utc)


class Project(db.Model):
    """
    A Journeyman automation project.

    A project contains one or more ordered playbook steps and uses one
    project-level inventory.
    """

    __tablename__ = "project"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
    )

    description = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    builtin_key = db.Column(
        db.String(120),
        nullable=True,
        unique=True,
        index=True,
    )

    inventory_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "inventory_source.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    repository_id = db.Column(
        db.Integer,
        db.ForeignKey("repository.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    environment_id = db.Column(
        db.Integer,
        db.ForeignKey("environment.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    execution_type = db.Column(
        db.String(20), nullable=False, default="ansible"
    )

    max_parallel_steps = db.Column(
        db.Integer,
        nullable=False,
        default=4,
    )

    concurrency_policy = db.Column(
        db.String(32),
        nullable=False,
        default="unrestricted",
    )

    oversight_required_between_all_steps = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    runner_routing = db.Column(
        db.String(24),
        nullable=False,
        default="local",
    )

    runner_site = db.Column(
        db.String(120),
        nullable=False,
        default="",
    )

    runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    default_runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    default_runner_crew_id = db.Column(
        db.Integer,
        db.ForeignKey("runner_crew.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    inventory = db.relationship(
        "Inventory",
        back_populates="projects",
    )

    repository = db.relationship("Repository")

    environment = db.relationship("Environment")

    runner = db.relationship("Runner", foreign_keys=[runner_id])

    default_runner = db.relationship(
        "Runner",
        foreign_keys=[default_runner_id],
    )

    default_runner_crew = db.relationship(
        "RunnerCrew",
        foreign_keys=[default_runner_crew_id],
        back_populates="projects",
    )

    credentials = db.relationship(
        "Credential",
        secondary=project_credential,
        order_by="Credential.name",
    )

    steps = db.relationship(
        "ProjectStep",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectStep.position",
    )

    jobs = db.relationship(
        "Job",
        back_populates="project",
    )

    schedules = db.relationship(
        "ProjectSchedule",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectSchedule.name",
    )

    packages = db.relationship(
        "ProjectPackage",
        back_populates="project",
        order_by="ProjectPackage.name",
    )

    owner = db.Column(
        db.String(255),
        nullable=False,
        default="system",
    )

    security_scope = db.Column(
        db.String(20),
        nullable=False,
        default="private",
    )

    def __repr__(self):
        return f"<Project {self.name!r}>"
