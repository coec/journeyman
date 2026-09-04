from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Runner(db.Model):
    """A Journeyman execution runner registered with the control plane."""

    __tablename__ = "runner"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    runner_uuid = db.Column(db.String(36), unique=True, nullable=True, index=True)
    hostname = db.Column(db.String(255), nullable=False, default="")
    site = db.Column(db.String(120), nullable=False, default="")
    capabilities_json = db.Column(db.Text, nullable=False, default="[]")
    # Feature/service capabilities are separate from execution capabilities.
    managed_capabilities_json = db.Column(db.Text, nullable=False, default="{}")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    # A draining runner remains authenticated so in-flight work can report
    # completion, but it is ineligible for new Job/slice/environment claims.
    drain_job_id = db.Column(db.Integer, nullable=True, index=True)
    drain_requested_at = db.Column(db.DateTime(timezone=True), nullable=True)
    drain_reason = db.Column(db.String(255), nullable=False, default="")
    is_local = db.Column(db.Boolean, nullable=False, default=False, index=True)
    max_concurrent_steps = db.Column(db.Integer, nullable=False, default=1)
    management_bootstrap_credential_id = db.Column(
        db.Integer,
        db.ForeignKey("credential.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    management_pip_proxy_required = db.Column(
        db.Boolean, nullable=False, default=False
    )
    management_pip_proxy_credential_id = db.Column(
        db.Integer,
        db.ForeignKey("credential.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    registration_token_digest = db.Column(db.String(64), nullable=False, default="")
    api_secret_digest = db.Column(db.String(64), nullable=False, default="")
    status_message = db.Column(db.Text, nullable=False, default="")
    version = db.Column(db.String(120), nullable=False, default="")
    runtime_dependencies_json = db.Column(db.Text, nullable=False, default="{}")
    runtime_dependencies_reported_at = db.Column(db.DateTime(timezone=True), nullable=True)
    runtime_dependency_audit_status = db.Column(db.String(32), nullable=False, default="unknown")
    runtime_dependency_audit_message = db.Column(db.Text, nullable=False, default="")
    runtime_dependency_audit_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    runtime_dependency_audit_fingerprint = db.Column(db.String(64), nullable=False, default="")
    runtime_dependency_audit_json = db.Column(db.Text, nullable=False, default="{}")
    running_steps = db.Column(db.Integer, nullable=False, default=0)
    load_average_1m = db.Column(db.Float, nullable=True)
    load_average_5m = db.Column(db.Float, nullable=True)
    cpu_count = db.Column(db.Integer, nullable=True)
    free_workspace_bytes = db.Column(db.BigInteger, nullable=True)
    registered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_heartbeat_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    crews = db.relationship(
        "RunnerCrew",
        secondary="runner_crew_member",
        back_populates="runners",
        order_by="RunnerCrew.name",
    )

    environment_states = db.relationship(
        "RunnerEnvironment",
        back_populates="runner",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RunnerEnvironment.environment_id",
    )

    environment_syncs = db.relationship(
        "RunnerEnvironmentSync",
        back_populates="runner",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RunnerEnvironmentSync.environment_id",
    )

    def capabilities(self):
        import json
        try:
            value = json.loads(self.capabilities_json or "[]")
        except (TypeError, ValueError):
            return set()
        return {str(item).strip().lower() for item in value if str(item).strip()}

    def set_capabilities(self, values):
        import json
        normalized = sorted({str(item).strip().lower() for item in values if str(item).strip()})
        self.capabilities_json = json.dumps(normalized)

    @property
    def is_registered(self):
        return bool(self.runner_uuid and self.api_secret_digest)
