from datetime import datetime, timezone
import json

from app import db


def utcnow():
    return datetime.now(timezone.utc)

project_step_credential = db.Table(
    "project_step_credential",
    db.Column(
        "project_step_id",
        db.Integer,
        db.ForeignKey(
            "project_step.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),
    db.Column(
        "credential_id",
        db.Integer,
        db.ForeignKey(
            "credential.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),
)
       

class ProjectStep(db.Model):
    """
    One ordered playbook execution within a Project.

    A single-playbook Project has one ProjectStep.
    A workflow Project has two or more ordered ProjectSteps.
    """

    __tablename__ = "project_step"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position = db.Column(
        db.Integer,
        nullable=False,
    )

    name = db.Column(
        db.String(120),
        nullable=False,
        default="",
    )

    repository_id = db.Column(
        db.Integer,
        db.ForeignKey("repository.id"),
        nullable=True,
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

    environment_id = db.Column(
        db.Integer,
        db.ForeignKey("environment.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    playbook = db.Column(
        db.String(500),
        nullable=False,
    )

    # Optional per-step overrides. Empty values inherit from Project.
    limit = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    tags = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    skip_tags = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    extra_vars_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    def get_extra_vars(self):
        try:
            value = json.loads(self.extra_vars_json or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def set_extra_vars(self, values):
        if not isinstance(values, dict):
            raise ValueError("Project step extra variables must be an object.")
        self.extra_vars_json = json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    verbosity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    check_mode = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    remote_shell_become = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    remote_shell_serial = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    continue_on_failure = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    failure_only = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    refresh_repository = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    refresh_inventory_after = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    oversight_after = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    credentials_override = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    depends_on_json = db.Column(
        db.Text,
        nullable=False,
        default="[]",
    )

    def get_dependency_positions(self):
        try:
            value = json.loads(self.depends_on_json or "[]")
        except (TypeError, ValueError):
            return []
        return [int(item) for item in value if str(item).isdigit()]

    def set_dependency_positions(self, positions):
        self.depends_on_json = json.dumps(sorted(set(int(p) for p in positions)))

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

    credentials = db.relationship(
        "Credential",
        secondary=project_step_credential,
        back_populates="project_steps",
        order_by="Credential.name",
    )

    project = db.relationship(
        "Project",
        back_populates="steps",
    )

    repository = db.relationship(
        "Repository",
    )

    environment = db.relationship(
        "Environment",
        back_populates="project_steps",
    )

    inventory = db.relationship(
        "Inventory",
        back_populates="project_steps",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "position",
            name="uq_project_step_position",
        ),
    )

    def effective_repository(self):
        return self.repository or self.project.repository

    def effective_environment(self):
        return self.environment or self.project.environment

    def effective_credentials(self):
        if self.credentials_override:
            return list(self.credentials)
        return list(self.project.credentials)

    def __repr__(self):
        return (
            f"<ProjectStep project_id={self.project_id} "
            f"position={self.position} playbook={self.playbook!r}>"
        )
