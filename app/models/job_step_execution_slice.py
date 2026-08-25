import json
from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class JobStepExecutionSlice(db.Model):
    """One independently dispatchable host slice of a Job step."""

    __tablename__ = "job_step_execution_slice"

    id = db.Column(db.Integer, primary_key=True)
    job_step_id = db.Column(
        db.Integer,
        db.ForeignKey("job_step.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = db.Column(db.Integer, nullable=False)
    dispatch_target = db.Column(
        db.String(20), nullable=False, default="local", index=True
    )
    required_runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_runner_id = db.Column(
        db.Integer,
        db.ForeignKey("runner.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    runner_name = db.Column(db.String(120), nullable=False, default="")
    runner_hostname = db.Column(db.String(255), nullable=False, default="")
    required_runner_capabilities_json = db.Column(
        db.Text, nullable=False, default="[]"
    )
    hosts_json = db.Column(db.Text, nullable=False, default="[]")
    host_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    exit_code = db.Column(db.Integer, nullable=True)
    dispatch_token = db.Column(db.String(64), nullable=False, default="", index=True)
    message = db.Column(db.Text, nullable=False, default="")
    command = db.Column(db.Text, nullable=False, default="")
    stdout = db.Column(db.Text, nullable=False, default="")
    stderr = db.Column(db.Text, nullable=False, default="")
    custom_stats_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow
    )

    step = db.relationship("JobStep", back_populates="execution_slices")
    required_runner = db.relationship("Runner", foreign_keys=[required_runner_id])
    assigned_runner = db.relationship("Runner", foreign_keys=[assigned_runner_id])

    __table_args__ = (
        db.UniqueConstraint(
            "job_step_id",
            "position",
            name="uq_job_step_execution_slice_position",
        ),
    )

    def get_hosts(self):
        try:
            value = json.loads(self.hosts_json or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def set_hosts(self, hosts):
        normalized = sorted({str(host).strip() for host in hosts if str(host).strip()})
        self.hosts_json = json.dumps(normalized, separators=(",", ":"))
        self.host_count = len(normalized)

    def get_required_capabilities(self):
        try:
            value = json.loads(self.required_runner_capabilities_json or "[]")
        except (TypeError, ValueError):
            return set()
        if not isinstance(value, list):
            return set()
        return {str(item).strip().lower() for item in value if str(item).strip()}

    def set_required_capabilities(self, capabilities):
        normalized = sorted(
            {
                str(capability).strip().lower()
                for capability in capabilities
                if str(capability).strip()
            }
        )
        self.required_runner_capabilities_json = json.dumps(
            normalized, separators=(",", ":")
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

    def __repr__(self):
        return (
            "<JobStepExecutionSlice job_step_id={} position={} "
            "dispatch_target={!r} status={!r}>"
        ).format(
            self.job_step_id,
            self.position,
            self.dispatch_target,
            self.status,
        )
