import json
from datetime import datetime, timezone
from app.security_scope import SECURITY_SCOPE_PRIVATE

from app import db


def utcnow():
    return datetime.now(timezone.utc)

job_step_credential_snapshot = db.Table(
    "job_step_credential_snapshot",

    db.Column(
        "job_step_id",
        db.Integer,
        db.ForeignKey(
            "job_step.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),

    db.Column(
        "job_credential_snapshot_id",
        db.Integer,
        db.ForeignKey(
            "job_credential_snapshot.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),
)

class Job(db.Model):
    """
    One execution of a Project.

    Job values are snapshots. Editing a Project later must not alter
    the historical record of what this Job executed.
    """

    __tablename__ = "job"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True,
    )

    project_name = db.Column(
        db.String(120),
        nullable=False,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default="queued",
        index=True,
    )

    requested_by = db.Column(
        db.String(255),
        nullable=False,
        default="system",
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

    concurrency_signature = db.Column(
        db.String(64),
        nullable=True,
        index=True,
    )

    oversight_required_between_all_steps = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    oversight_reviewer = db.Column(
        db.String(255),
        nullable=False,
        default="",
    )

    queued_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    finished_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    cancel_requested_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    exit_code = db.Column(
        db.Integer,
        nullable=True,
    )

    message = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    dispatch_target = db.Column(
        db.String(20), nullable=False, default="local", index=True
    )

    required_runner_site = db.Column(
        db.String(120), nullable=False, default=""
    )

    required_runner_id = db.Column(
        db.Integer, db.ForeignKey("runner.id"), nullable=True, index=True
    )

    # Snapshot of the Project default runner at queue time.  Sliced Jobs use
    # this when later inventory refreshes require runner routing to be planned
    # again; editing the Project must not change an already-queued Job.
    default_runner_id = db.Column(
        db.Integer, db.ForeignKey("runner.id"), nullable=True, index=True
    )

    # Snapshot of the Project default Runner Crew at queue time.  This keeps
    # mid-workflow inventory refresh routing independent of later Project edits.
    default_runner_crew_id = db.Column(
        db.Integer, db.ForeignKey("runner_crew.id"), nullable=True, index=True
    )

    required_runner_capabilities_json = db.Column(
        db.Text, nullable=False, default="[]"
    )

    assigned_runner_id = db.Column(
        db.Integer, db.ForeignKey("runner.id"), nullable=True, index=True
    )

    assigned_at = db.Column(
        db.DateTime(timezone=True), nullable=True
    )

    dispatch_token = db.Column(
        db.String(64), nullable=False, default="", index=True
    )

    assigned_runner = db.relationship("Runner", foreign_keys=[assigned_runner_id])
    required_runner = db.relationship("Runner", foreign_keys=[required_runner_id])
    default_runner = db.relationship("Runner", foreign_keys=[default_runner_id])
    default_runner_crew = db.relationship("RunnerCrew", foreign_keys=[default_runner_crew_id])

    project = db.relationship(
        "Project",
        back_populates="jobs",
    )

    steps = db.relationship(
        "JobStep",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStep.position",
    )

    repository_snapshots = db.relationship(
        "JobRepositorySnapshot",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobRepositorySnapshot.id",
    )

    credential_snapshots = db.relationship(
        "JobCredentialSnapshot",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobCredentialSnapshot.id",
    )

    inventory_snapshots = db.relationship(
        "JobInventorySnapshot",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobInventorySnapshot.version",
    )

    package_snapshot = db.relationship(
        "JobPackageSnapshot",
        back_populates="job",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def duration_seconds(self):
        if self.started_at is None:
            return None

        end_time = self.finished_at or datetime.now(
            timezone.utc
        )

        started_at = self.started_at

        if started_at.tzinfo is None:
            started_at = started_at.replace(
                tzinfo=timezone.utc
            )

        if end_time.tzinfo is None:
            end_time = end_time.replace(
                tzinfo=timezone.utc
            )

        return max(
            0,
            int((end_time - started_at).total_seconds()),
        )

    def __repr__(self):
        return f"<Job id={self.id} status={self.status!r}>"


class JobStep(db.Model):
    """
    Immutable execution snapshot of one ProjectStep.
    """

    __tablename__ = "job_step"

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

    project_step_id = db.Column(
        db.Integer,
        nullable=True,
    )

    job_repository_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job_repository_snapshot.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    job_inventory_snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job_inventory_snapshot.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
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

    environment_name = db.Column(
        db.String(120),
        nullable=False,
        default="",
    )

    environment_id = db.Column(
        db.Integer,
        db.ForeignKey("environment.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    environment_revision = db.Column(
        db.String(64),
        nullable=False,
        default="",
    )

    environment_path = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    ansible_config_path = db.Column(
        db.String(1000),
        nullable=False,
        default="/etc/ansible/ansible.cfg",
    )

    playbook = db.Column(
        db.String(500),
        nullable=False,
    )

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
            raise ValueError("Job step extra variables must be an object.")
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

    refresh_inventory_after = db.Column(
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

    oversight_required_before = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    oversight_approved = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default="pending",
    )

    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    finished_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    exit_code = db.Column(
        db.Integer,
        nullable=True,
    )

    command = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    stdout = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    stderr = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    custom_stats_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    def get_custom_stats(self):
        try:
            value = json.loads(self.custom_stats_json or "{}")
        except (TypeError, ValueError):
            return {}

        return value if isinstance(value, dict) else {}

    def set_custom_stats(self, value):
        self.custom_stats_json = json.dumps(
            value or {},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    job = db.relationship(
        "Job",
        back_populates="steps",
    )

    repository_snapshot = db.relationship(
        "JobRepositorySnapshot",
    )

    inventory_snapshot = db.relationship(
        "JobInventorySnapshot",
        back_populates="steps",
    )

    environment = db.relationship(
        "Environment",
        foreign_keys=[environment_id],
    )

    credential_snapshots = db.relationship(
        "JobCredentialSnapshot",
        secondary=job_step_credential_snapshot,
        order_by="JobCredentialSnapshot.id",
    )

    host_results = db.relationship(
        "JobStepHostResult",
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="JobStepHostResult.host",
    )

    execution_slices = db.relationship(
        "JobStepExecutionSlice",
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="JobStepExecutionSlice.position",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "job_id",
            "position",
            name="uq_job_step_position",
        ),
    )

    def __repr__(self):
        return (
            f"<JobStep job_id={self.job_id} "
            f"position={self.position} status={self.status!r}>"
        )
