from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Environment(db.Model):
    """A Python virtual environment available to Journeyman jobs."""

    __tablename__ = "environment"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    path = db.Column(db.String(1000), unique=True, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_builtin = db.Column(db.Boolean, nullable=False, default=False)
    is_managed = db.Column(db.Boolean, nullable=False, default=False)
    python_interpreter = db.Column(db.String(1000), nullable=False, default="")
    ansible_spec = db.Column(db.String(255), nullable=False, default="ansible-core")
    ansible_config_path = db.Column(
        db.String(1000), nullable=False, default="/etc/ansible/ansible.cfg"
    )
    pip_requirements = db.Column(db.Text, nullable=False, default="")
    system_requirements = db.Column(db.Text, nullable=False, default="")
    collection_requirements = db.Column(db.Text, nullable=False, default="")
    proxy_credential_id = db.Column(
        db.Integer,
        db.ForeignKey("credential.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    proxy_credential = db.relationship("Credential", foreign_keys=[proxy_credential_id])
    build_status = db.Column(db.String(32), nullable=False, default="not_built")
    build_message = db.Column(db.Text, nullable=False, default="")
    python_version = db.Column(db.String(120), nullable=False, default="")
    ansible_version = db.Column(db.String(120), nullable=False, default="")
    validation_status = db.Column(db.String(32), nullable=False, default="not_tested")
    validation_message = db.Column(db.Text, nullable=False, default="")
    last_validated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project_steps = db.relationship("ProjectStep", back_populates="environment")
    runner_states = db.relationship(
        "RunnerEnvironment",
        back_populates="environment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RunnerEnvironment.runner_id",
    )

    runner_syncs = db.relationship(
        "RunnerEnvironmentSync",
        back_populates="environment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RunnerEnvironmentSync.runner_id",
    )

    def __repr__(self):
        return f"<Environment {self.name!r} path={self.path!r}>"
