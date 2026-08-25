import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import object_session, validates

from app import db
from app.credential_crypto import (
    decrypt_credential_data,
    encrypt_credential_data_with_key_id,
)

SOURCE_ZABBIX = "zabbix"
SOURCE_SYSLOG = "syslog"
SOURCE_SNMP_TRAP = "snmp_trap"
VALID_SOURCE_TYPES = {SOURCE_ZABBIX, SOURCE_SYSLOG, SOURCE_SNMP_TRAP}

REACTOR_OBSERVE = "observe"
REACTOR_AUTOMATIC = "automatic"
VALID_REACTOR_MODES = {REACTOR_OBSERVE, REACTOR_AUTOMATIC}

REACTION_OBSERVED = "observed"
REACTION_PENDING = "pending"
REACTION_QUEUED = "queued"
REACTION_RUNNING = "running"
REACTION_CANCELLING = "cancelling"
REACTION_SUCCESSFUL = "successful"
REACTION_CANCELLED = "cancelled"
REACTION_SUPPRESSED = "suppressed"
REACTION_FAILED = "failed"


def utcnow():
    return datetime.now(timezone.utc)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw, default):
    try:
        value = json.loads(raw or _dump(default))
    except json.JSONDecodeError as exc:
        raise ValueError("Stored Reaction JSON is invalid.") from exc
    return value


class SignalSource(db.Model):
    __tablename__ = "signal_source"
    __table_args__ = (db.UniqueConstraint("name", name="uq_signal_source_name"),)

    id = db.Column(db.Integer, primary_key=True)
    source_uuid = db.Column(db.String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(120), nullable=False)
    source_type = db.Column(db.String(32), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    description = db.Column(db.String(500), nullable=False, default="")
    allowed_networks_json = db.Column(db.Text, nullable=False, default="[]")

    # Zabbix-specific configuration. URL is identity/documentation only; inbound
    # authentication is HMAC + source/network policy, never reverse-DNS matching.
    zabbix_url = db.Column(db.String(1000), nullable=False, default="")
    encrypted_hmac_secret = db.Column(db.LargeBinary, nullable=True)
    hmac_secret_key_id = db.Column(db.String(120), nullable=True)
    secret_created_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Runner-received Signals are forwarded by an already-authenticated remote runner.
    runner_id = db.Column(db.Integer, db.ForeignKey("runner.id", ondelete="RESTRICT"), nullable=True, index=True)

    # SNMP Trap Sources use a dedicated snmptrapd instance on the assigned Runner.
    # Keeping one UDP port per Source makes Source identity unambiguous before the
    # Signal reaches Journeyman.
    snmp_port = db.Column(db.Integer, nullable=False, default=162)

    last_signal_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_sender_ip = db.Column(db.String(64), nullable=False, default="")
    accepted_count = db.Column(db.Integer, nullable=False, default=0)
    rejected_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    runner = db.relationship("Runner")
    signals = db.relationship("Signal", back_populates="source", passive_deletes=True)
    reactors = db.relationship("Reactor", back_populates="source", passive_deletes=True)

    @validates("source_type")
    def validate_source_type(self, key, value):
        value = str(value or "").strip().lower()
        if value not in VALID_SOURCE_TYPES:
            raise ValueError("Invalid Source type: {!r}".format(value))
        return value

    def get_allowed_networks(self):
        value = _load(self.allowed_networks_json, [])
        if not isinstance(value, list):
            raise ValueError("Source allowed networks must be a list.")
        return [str(item).strip() for item in value if str(item).strip()]

    def set_allowed_networks(self, values):
        if not isinstance(values, list):
            raise ValueError("Source allowed networks must be a list.")
        self.allowed_networks_json = _dump([str(item).strip() for item in values if str(item).strip()])

    def set_hmac_secret(self, secret):
        secret = str(secret or "").strip()
        if not secret:
            raise ValueError("HMAC secret is required.")
        encrypted, key_id = encrypt_credential_data_with_key_id({"secret": secret})
        self.encrypted_hmac_secret = encrypted
        self.hmac_secret_key_id = key_id
        self.secret_created_at = utcnow()

    def get_hmac_secret(self):
        if not self.encrypted_hmac_secret:
            return ""
        return str(decrypt_credential_data(self.encrypted_hmac_secret, self.hmac_secret_key_id).get("secret") or "")


class Signal(db.Model):
    __tablename__ = "signal"
    __table_args__ = (
        db.UniqueConstraint("source_id", "external_signal_id", name="uq_signal_source_external_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("signal_source.id", ondelete="RESTRICT"), nullable=False, index=True)
    external_signal_id = db.Column(db.String(255), nullable=False)
    signal_type = db.Column(db.String(120), nullable=False, default="")
    signal_at = db.Column(db.DateTime(timezone=True), nullable=True)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    host = db.Column(db.String(255), nullable=False, default="", index=True)
    severity = db.Column(db.String(64), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    fields_json = db.Column(db.Text, nullable=False, default="{}")
    raw_payload = db.Column(db.Text, nullable=False, default="")
    sender_ip = db.Column(db.String(64), nullable=False, default="")
    runner_id = db.Column(db.Integer, db.ForeignKey("runner.id", ondelete="SET NULL"), nullable=True, index=True)
    processing_status = db.Column(db.String(32), nullable=False, default="accepted")

    source = db.relationship("SignalSource", back_populates="signals")
    runner = db.relationship("Runner")
    reactions = db.relationship(
        "Reaction",
        back_populates="signal",
        cascade="all, delete-orphan",
        foreign_keys="Reaction.signal_id",
    )

    def get_fields(self):
        value = _load(self.fields_json, {})
        if not isinstance(value, dict):
            raise ValueError("Signal fields must be an object.")
        return value

    def set_fields(self, value):
        if not isinstance(value, dict):
            raise ValueError("Signal fields must be an object.")
        self.fields_json = _dump(value)


class Reactor(db.Model):
    __tablename__ = "reactor"
    __table_args__ = (db.UniqueConstraint("name", name="uq_reactor_name"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    mode = db.Column(db.String(32), nullable=False, default=REACTOR_OBSERVE)
    source_id = db.Column(db.Integer, db.ForeignKey("signal_source.id", ondelete="RESTRICT"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("project_package.id", ondelete="RESTRICT"), nullable=False, index=True)
    match_json = db.Column(db.Text, nullable=False, default='{"all":[]}')
    mappings_json = db.Column(db.Text, nullable=False, default="{}")
    recovery_window_seconds = db.Column(db.Integer, nullable=False, default=0)
    recovery_match_json = db.Column(db.Text, nullable=False, default='{"all":[]}')
    recovery_correlation_inputs_json = db.Column(db.Text, nullable=False, default="[]")
    cooldown_seconds = db.Column(db.Integer, nullable=False, default=0)
    max_concurrency = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    source = db.relationship("SignalSource", back_populates="reactors")
    package = db.relationship("ProjectPackage", back_populates="reactors")
    reactions = db.relationship("Reaction", back_populates="reactor", passive_deletes=True)

    @validates("mode")
    def validate_mode(self, key, value):
        value = str(value or "").strip().lower()
        if value not in VALID_REACTOR_MODES:
            raise ValueError("Invalid Reactor mode: {!r}".format(value))
        return value

    def get_match(self):
        value = _load(self.match_json, {"all": []})
        if not isinstance(value, dict):
            raise ValueError("Reactor match definition must be an object.")
        return value

    def set_match(self, value):
        if not isinstance(value, dict):
            raise ValueError("Reactor match definition must be an object.")
        self.match_json = _dump(value)

    def get_mappings(self):
        value = _load(self.mappings_json, {})
        if not isinstance(value, dict):
            raise ValueError("Reactor mappings must be an object.")
        return value

    def set_mappings(self, value):
        if not isinstance(value, dict):
            raise ValueError("Reactor mappings must be an object.")
        self.mappings_json = _dump(value)

    def get_recovery_match(self):
        value = _load(self.recovery_match_json, {"all": []})
        if not isinstance(value, dict):
            raise ValueError("Reactor recovery match definition must be an object.")
        return value

    def set_recovery_match(self, value):
        if not isinstance(value, dict):
            raise ValueError("Reactor recovery match definition must be an object.")
        self.recovery_match_json = _dump(value)

    def get_recovery_correlation_inputs(self):
        value = _load(self.recovery_correlation_inputs_json, [])
        if not isinstance(value, list):
            raise ValueError("Reactor recovery correlation inputs must be a list.")
        return [str(item).strip() for item in value if str(item).strip()]

    def set_recovery_correlation_inputs(self, values):
        if not isinstance(values, list):
            raise ValueError("Reactor recovery correlation inputs must be a list.")
        normalised = []
        for value in values:
            value = str(value or "").strip()
            if value and value not in normalised:
                normalised.append(value)
        self.recovery_correlation_inputs_json = _dump(normalised)


class Reaction(db.Model):
    __tablename__ = "reaction"
    __table_args__ = (
        db.UniqueConstraint("signal_id", "reactor_id", name="uq_reaction_signal_reactor"),
    )

    id = db.Column(db.Integer, primary_key=True)
    signal_id = db.Column(db.Integer, db.ForeignKey("signal.id", ondelete="CASCADE"), nullable=False, index=True)
    reactor_id = db.Column(db.Integer, db.ForeignKey("reactor.id", ondelete="SET NULL"), nullable=True, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("project_package.id", ondelete="SET NULL"), nullable=True, index=True)
    reactor_name_snapshot = db.Column(db.String(120), nullable=False, default="")
    source_name_snapshot = db.Column(db.String(120), nullable=False, default="")
    package_name_snapshot = db.Column(db.String(120), nullable=False, default="")
    job_id = db.Column(db.Integer, db.ForeignKey("job.id", ondelete="SET NULL"), nullable=True, index=True)
    recovery_signal_id = db.Column(db.Integer, db.ForeignKey("signal.id", ondelete="SET NULL"), nullable=True, index=True)
    mode = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    resolved_inputs_json = db.Column(db.Text, nullable=False, default="{}")
    message = db.Column(db.Text, nullable=False, default="")
    execute_after = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    suppressed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    signal = db.relationship("Signal", back_populates="reactions", foreign_keys=[signal_id])
    recovery_signal = db.relationship("Signal", foreign_keys=[recovery_signal_id])
    reactor = db.relationship("Reactor", back_populates="reactions")
    package = db.relationship("ProjectPackage")
    job = db.relationship("Job")

    @property
    def reactor_display_name(self):
        if self.reactor is not None:
            return self.reactor.name
        name = str(self.reactor_name_snapshot or "").strip() or "Deleted Reactor"
        return "{} (deleted)".format(name)

    @property
    def source_display_name(self):
        if self.signal is not None and self.signal.source is not None:
            return self.signal.source.name
        return str(self.source_name_snapshot or "").strip() or "—"

    @property
    def package_display_name(self):
        if self.package is not None:
            return self.package.name
        name = str(self.package_name_snapshot or "").strip()
        return "{} (deleted)".format(name) if name else "—"

    def get_resolved_inputs(self):
        value = _load(self.resolved_inputs_json, {})
        return value if isinstance(value, dict) else {}

    def set_resolved_inputs(self, value):
        if not isinstance(value, dict):
            raise ValueError("Reaction resolved inputs must be an object.")
        self.resolved_inputs_json = _dump(value)


_JOB_REACTION_STATUS = {
    "queued": REACTION_QUEUED,
    "running": REACTION_RUNNING,
    "cancelling": REACTION_CANCELLING,
    "successful": REACTION_SUCCESSFUL,
    "failed": REACTION_FAILED,
    "cancelled": REACTION_CANCELLED,
}


def sync_reaction_for_job(job, status=None):
    """Persist the Reaction lifecycle state that corresponds to a linked Job.

    Reaction is an audit record in its own right.  Once an Automatic Reactor
    queues a normal Journeyman Job, keep that audit record synchronized with
    the Job rather than leaving it permanently at ``queued``.
    """

    if job is None or job.id is None:
        return None

    session = object_session(job)
    if session is None:
        return None

    job_status = str(status if status is not None else job.status or "").strip().lower()
    reaction_status = _JOB_REACTION_STATUS.get(job_status)
    if reaction_status is None:
        return None

    reaction = (
        session.query(Reaction)
        .filter(Reaction.job_id == job.id)
        .one_or_none()
    )
    if reaction is None:
        return None

    reaction.status = reaction_status
    reaction.message = {
        "queued": "Reaction queued as Job #{}.",
        "running": "Reaction running as Job #{}.",
        "cancelling": "Reaction Job #{} is being cancelled.",
        "successful": "Reaction completed successfully as Job #{}.",
        "failed": "Reaction failed as Job #{}.",
        "cancelled": "Reaction cancelled as Job #{}.",
    }[job_status].format(job.id)
    return reaction


# Most Job transitions use ordinary ORM attribute assignment.  Listen at the
# model boundary so local execution, sliced execution, cancellation and failure
# paths all update the linked Reaction in the same transaction.
from app.models.job import Job  # imported late to avoid model import cycles


@event.listens_for(Job.status, "set", active_history=True)
def _sync_reaction_when_job_status_changes(job, value, oldvalue, initiator):
    if value == oldvalue:
        return
    sync_reaction_for_job(job, status=value)
