"""Project-level concurrent execution policy."""

import hashlib
import hmac
import json

from flask import current_app

from app.models import Job, Project


CONCURRENCY_UNRESTRICTED = "unrestricted"
CONCURRENCY_DISTINCT = "distinct_parameters"
CONCURRENCY_SERIALIZED = "serialized"
CONCURRENCY_EXCLUSIVE = "exclusive"
PROJECT_CONCURRENCY_POLICIES = (
    CONCURRENCY_UNRESTRICTED,
    CONCURRENCY_DISTINCT,
    CONCURRENCY_SERIALIZED,
    CONCURRENCY_EXCLUSIVE,
)

ACTIVE_PROJECT_JOB_STATUSES = (
    "queued",
    "running",
    "waiting_oversight",
    "cancelling",
)
EXECUTING_PROJECT_JOB_STATUSES = (
    "running",
    "waiting_oversight",
    "cancelling",
)


def normalise_concurrency_policy(value):
    value = str(value or CONCURRENCY_UNRESTRICTED).strip().lower()
    if value not in PROJECT_CONCURRENCY_POLICIES:
        raise ValueError("Project concurrency policy is invalid.")
    return value


def locked_project(project):
    """Return ``project`` after taking a dispatch lock where supported."""
    if project is None or project.id is None:
        return project
    return (
        Project.query
        .filter(Project.id == project.id)
        .with_for_update()
        .one()
    )


def parameter_signature(package_execution=None):
    """Return an HMAC fingerprint of effective launch parameters.

    The Package identity is deliberately excluded: concurrency belongs to the
    Project, so two Packages with the same effective inputs compare equal.
    HMAC prevents stored fingerprints of secret Package inputs from becoming
    useful offline guessing targets.
    """
    if package_execution is None:
        payload = {
            "execution_vars": {},
            "inventory_bindings": {},
            "step_limit": "",
            "machine_credential_override_id": None,
        }
    else:
        payload = {
            "execution_vars": package_execution.execution_vars,
            "inventory_bindings": package_execution.inventory_bindings,
            "step_limit": package_execution.step_limit or "",
            "machine_credential_override_id": (
                package_execution.machine_credential_override_id
            ),
        }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    secret = str(current_app.config.get("SECRET_KEY") or "").encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def parameter_signature_for_job(job):
    if job is None:
        return None
    if job.concurrency_signature:
        return job.concurrency_signature
    snapshot = job.package_snapshot
    if snapshot is None:
        return parameter_signature(None)

    class _SavedExecution:
        execution_vars = snapshot.get_execution_vars()
        inventory_bindings = snapshot.get_inventory_bindings()
        step_limit = snapshot.step_limit or ""
        machine_credential_override_id = None

    return parameter_signature(_SavedExecution())


def scoped_parameter_signature(base_signature, *, scope, hosts=()):
    """Derive a concurrency fingerprint for a narrowed rerun target set."""
    payload = {
        "base_signature": str(base_signature or ""),
        "scope": str(scope or "all"),
        "hosts": sorted({str(host) for host in hosts if str(host).strip()}),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    secret = str(current_app.config.get("SECRET_KEY") or "").encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def launch_blocking_job(project, policy, signature, *, exclude_job_id=None):
    """Return the active Job that rejects a new launch, if any."""
    policy = normalise_concurrency_policy(policy)
    if project is None or project.id is None or policy in {
        CONCURRENCY_UNRESTRICTED, CONCURRENCY_SERIALIZED
    }:
        return None
    query = Job.query.filter(
        Job.project_id == project.id,
        Job.status.in_(ACTIVE_PROJECT_JOB_STATUSES),
    )
    if exclude_job_id is not None:
        query = query.filter(Job.id != exclude_job_id)
    if policy == CONCURRENCY_DISTINCT:
        # A pre-upgrade active Job has no fingerprint, so conservatively block
        # until it finishes rather than risk a duplicate execution.
        query = query.filter(
            (Job.concurrency_signature == signature)
            | Job.concurrency_signature.is_(None)
        )
    return query.order_by(Job.queued_at.asc(), Job.id.asc()).first()


def serialized_blocking_job(job):
    """Return the Job that must finish before this serialized Job may start."""
    if job is None or job.id is None or job.concurrency_policy != CONCURRENCY_SERIALIZED:
        return None
    others = (
        Job.query
        .filter(
            Job.project_id == job.project_id,
            Job.id != job.id,
            Job.status.in_(ACTIVE_PROJECT_JOB_STATUSES),
        )
        .order_by(Job.queued_at.asc(), Job.id.asc())
        .all()
    )
    for other in others:
        if other.status in EXECUTING_PROJECT_JOB_STATUSES:
            return other
        if other.status == "queued":
            if (other.queued_at, other.id) < (job.queued_at, job.id):
                return other
    return None


def job_can_start(job):
    return serialized_blocking_job(job) is None


def project_concurrency_message(project, policy, blocker):
    status = str(blocker.status or "active").replace("_", " ")
    if policy == CONCURRENCY_DISTINCT:
        return (
            'Project "{}" uses Distinct parameters concurrency and Job #{} '
            'is already {} with the same effective parameters.'
        ).format(project.name, blocker.id, status)
    return (
        'Project "{}" uses Exclusive concurrency and Job #{} is currently {}.'
    ).format(project.name, blocker.id, status)
