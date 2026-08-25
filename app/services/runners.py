import hashlib
import os
import hmac
import secrets
import socket
import uuid
from datetime import datetime, timezone

from app import db
from app.models import Job, JobStepExecutionSlice, JobStepHostResult, Project, Runner


CURRENT_REMOTE_RUNNER_VERSION = "0.16"


def _runner_version_key(value):
    """Return a comparable numeric runner version tuple when possible."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


def runner_update_available(runner):
    """Return whether a registered remote runner should be updated.

    Unknown/non-numeric versions are treated as updateable so an administrator
    can restore the runner to the version bundled with this Journeyman server.
    A runner reporting a newer numeric version is never offered a downgrade.
    """

    if runner.is_local or not runner.is_registered:
        return False
    current = _runner_version_key(CURRENT_REMOTE_RUNNER_VERSION)
    reported = _runner_version_key(runner.version)
    if reported is None:
        return True
    if current is None:
        return str(runner.version or "").strip() != CURRENT_REMOTE_RUNNER_VERSION
    return reported < current


class RunnerRemovalError(RuntimeError):
    """Raised when a runner cannot safely be removed from the registry."""


def runner_removal_references(runner):
    """Return control-plane records that still refer to ``runner``.

    Live Project/Job references block a hard delete. Historical execution
    slices and host results are reported for visibility but retain snapshot
    runner identity and may safely have their live runner foreign keys nulled.
    """

    project_count = Project.query.filter(
        (Project.default_runner_id == runner.id)
        | (Project.runner_id == runner.id)
    ).count()
    job_count = Job.query.filter(
        (Job.required_runner_id == runner.id)
        | (Job.default_runner_id == runner.id)
        | (Job.assigned_runner_id == runner.id)
    ).count()
    slice_count = JobStepExecutionSlice.query.filter(
        (JobStepExecutionSlice.required_runner_id == runner.id)
        | (JobStepExecutionSlice.assigned_runner_id == runner.id)
    ).count()
    host_result_count = JobStepHostResult.query.filter_by(runner_id=runner.id).count()
    return {
        "projects": project_count,
        "jobs": job_count,
        "slices": slice_count,
        "host_results": host_result_count,
    }


def find_runner_for_management(reference):
    """Resolve a human-entered runner name/hostname/UUID unambiguously.

    Built-in runner management commonly receives the node FQDN as its target.
    Prefer exact matches, then allow a unique short-hostname match so a runner
    named ``runner01`` can be managed using ``runner01.example.com``.
    """

    value = str(reference or "").strip()
    if not value:
        raise RunnerRemovalError("Runner name or hostname is required.")

    exact = Runner.query.filter(
        (Runner.name == value)
        | (Runner.hostname == value)
        | (Runner.runner_uuid == value)
    ).all()
    exact = list({runner.id: runner for runner in exact}.values())
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RunnerRemovalError(
            'Runner reference "{}" matches more than one registered runner.'.format(value)
        )

    short = value.split(".", 1)[0].lower()
    candidates = []
    for runner in Runner.query.all():
        names = [runner.name, runner.hostname]
        if any(
            str(item or "").split(".", 1)[0].lower() == short
            for item in names
            if str(item or "").strip()
        ):
            candidates.append(runner)

    candidates = list({runner.id: runner for runner in candidates}.values())
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RunnerRemovalError(
            'Runner reference "{}" is ambiguous. Use the exact runner name, FQDN, or UUID.'.format(value)
        )
    raise RunnerRemovalError(
        'No registered Journeyman runner matches "{}".'.format(value)
    )


def ensure_remote_management_target(reference):
    """Reject localhost/the Journeyman control-plane host as a managed runner target.

    Built-in runner management may delete software and service accounts, so it must
    never be allowed to point back at the node running Journeyman itself.  Compare
    obvious localhost aliases, the registered local runner hostname, local hostname/
    FQDN, and (best effort) resolved addresses.
    """

    value = str(reference or "").strip()
    if not value:
        raise RunnerRemovalError("Runner host is required.")

    candidate = value.rstrip(".").lower()
    candidate_short = candidate.split(".", 1)[0]
    if candidate in {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}:
        raise RunnerRemovalError(
            "Manage Remote Runner cannot target localhost or the Journeyman server itself."
        )

    local_names = {"localhost", "localhost.localdomain"}
    for name in (socket.gethostname(), socket.getfqdn()):
        normalized = str(name or "").strip().rstrip(".").lower()
        if normalized:
            local_names.add(normalized)
            local_names.add(normalized.split(".", 1)[0])

    local_runner = Runner.query.filter_by(is_local=True).one_or_none()
    if local_runner is not None:
        for name in (local_runner.hostname, local_runner.name):
            normalized = str(name or "").strip().rstrip(".").lower()
            if normalized:
                local_names.add(normalized)
                local_names.add(normalized.split(".", 1)[0])

    if candidate in local_names or candidate_short in local_names:
        raise RunnerRemovalError(
            "Manage Remote Runner cannot target the Journeyman server itself."
        )

    def resolved_addresses(name):
        addresses = set()
        try:
            for info in socket.getaddrinfo(name, None):
                address = info[4][0]
                if address:
                    addresses.add(str(address).split("%", 1)[0].lower())
        except OSError:
            pass
        return addresses

    candidate_addresses = resolved_addresses(value)
    if any(address == "::1" or address.startswith("127.") for address in candidate_addresses):
        raise RunnerRemovalError(
            "Manage Remote Runner cannot target localhost or the Journeyman server itself."
        )

    local_addresses = set()
    for name in local_names:
        local_addresses.update(resolved_addresses(name))
    local_addresses = {
        address for address in local_addresses
        if address != "::1" and not address.startswith("127.")
    }
    if candidate_addresses and candidate_addresses.intersection(local_addresses):
        raise RunnerRemovalError(
            "Manage Remote Runner cannot target the Journeyman server itself."
        )

    return value


def unregister_runner(runner):
    """Revoke a remote runner's credentials while retaining its history row."""

    if runner.is_local:
        raise RunnerRemovalError("The built-in local runner cannot be unregistered.")
    runner.enabled = False
    runner.registration_token_digest = ""
    runner.api_secret_digest = ""
    runner.runner_uuid = None
    runner.running_steps = 0
    runner.status_message = "Unregistered"
    db.session.commit()
    return runner


def delete_runner(runner):
    """Delete an unused remote runner without destroying Project/Job history."""

    if runner.is_local:
        raise RunnerRemovalError("The built-in local runner cannot be deleted.")
    references = runner_removal_references(runner)
    if references["projects"] or references["jobs"]:
        parts = []
        if references["projects"]:
            parts.append(
                "{} Project{}".format(
                    references["projects"],
                    "" if references["projects"] == 1 else "s",
                )
            )
        if references["jobs"]:
            parts.append(
                "{} Job{}".format(
                    references["jobs"],
                    "" if references["jobs"] == 1 else "s",
                )
            )
        raise RunnerRemovalError(
            "Runner is still referenced by {}. Unregister it first if it must "
            "stop accepting work, then change Project defaults. Historical "
            "execution slices and host results retain snapshot runner provenance "
            "after the runner registry row is deleted.".format(
                " and ".join(parts)
            )
        )

    # Historical execution records retain their snapshotted runner
    # name/hostname, but must not retain a live foreign-key reference to a
    # Runner registry row that is about to be deleted.  Do this explicitly
    # instead of relying solely on database ON DELETE SET NULL behaviour:
    # SQLite foreign-key enforcement can be disabled per connection, and the
    # service contract should be identical on SQLite and PostgreSQL.
    JobStepExecutionSlice.query.filter(
        JobStepExecutionSlice.required_runner_id == runner.id
    ).update(
        {JobStepExecutionSlice.required_runner_id: None},
        synchronize_session="fetch",
    )
    JobStepExecutionSlice.query.filter(
        JobStepExecutionSlice.assigned_runner_id == runner.id
    ).update(
        {JobStepExecutionSlice.assigned_runner_id: None},
        synchronize_session="fetch",
    )
    JobStepHostResult.query.filter(
        JobStepHostResult.runner_id == runner.id
    ).update(
        {JobStepHostResult.runner_id: None},
        synchronize_session="fetch",
    )
    db.session.delete(runner)
    db.session.commit()

RUNNER_HEARTBEAT_WARNING_SECONDS = 90
RUNNER_HEARTBEAT_OFFLINE_SECONDS = 180


def utcnow():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_registration_token(runner):
    token = secrets.token_urlsafe(32)
    runner.registration_token_digest = _digest(token)
    return token


def register_runner(token, *, hostname="", version=""):
    """Consume a one-time token for initial registration or credential recovery.

    Issuing a new registration token does not revoke an already-running
    runner's current API credential. Only successful consumption rotates the
    runner UUID and API secret, which keeps update-time recovery failure-safe
    until the remote host has actually obtained replacement credentials.
    """

    digest = _digest(token or "")
    runner = Runner.query.filter_by(registration_token_digest=digest).one_or_none()
    if runner is None or not runner.enabled:
        return None, None

    secret = secrets.token_urlsafe(48)
    runner.runner_uuid = str(uuid.uuid4())
    runner.api_secret_digest = _digest(secret)
    runner.registration_token_digest = ""
    runner.hostname = str(hostname or "")[:255]
    runner.version = str(version or "")[:120]
    runner.runtime_dependencies_json = "{}"
    runner.runtime_dependencies_reported_at = None
    runner.runtime_dependency_audit_status = "pending"
    runner.runtime_dependency_audit_message = "Awaiting runtime dependency report from runner."
    runner.runtime_dependency_audit_checked_at = None
    runner.runtime_dependency_audit_fingerprint = ""
    runner.runtime_dependency_audit_json = "{}"
    runner.registered_at = utcnow()
    runner.last_heartbeat_at = utcnow()
    runner.status_message = "Registered; waiting for work dispatch support."
    db.session.commit()
    return runner, secret


def authenticate_runner(runner_uuid, secret):
    runner = Runner.query.filter_by(runner_uuid=runner_uuid).one_or_none()
    if runner is None or not runner.enabled or not runner.api_secret_digest:
        return None
    if not hmac.compare_digest(runner.api_secret_digest, _digest(secret or "")):
        return None
    return runner


def runner_health(runner, now=None):
    if not runner.enabled:
        return "disabled"
    if runner.is_local and runner.status_message == "Stopped":
        return "offline"
    if not runner.is_local and not runner.is_registered:
        return "pending"
    if runner.last_heartbeat_at is None:
        return "offline"
    now = now or utcnow()
    heartbeat = runner.last_heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    age = max(0, (now - heartbeat).total_seconds())
    if age >= RUNNER_HEARTBEAT_OFFLINE_SECONDS:
        return "offline"
    if age >= RUNNER_HEARTBEAT_WARNING_SECONDS:
        return "warning"
    return "healthy"


def update_local_runner_heartbeat(
    *,
    hostname=None,
    version="",
    running_jobs=0,
    status_message="Ready",
    stopped=False,
):
    """Create or refresh the built-in local runner status row."""

    hostname = str(hostname or socket.gethostname() or "localhost")[:255]
    runner_uuid = "local:{}".format(hostname)[:36]
    runner = Runner.query.filter_by(is_local=True).one_or_none()
    if runner is None:
        runner = Runner(
            name="{} local runner".format(hostname),
            runner_uuid=runner_uuid,
            hostname=hostname,
            site="local",
            enabled=True,
            is_local=True,
            max_concurrent_steps=1,
            registered_at=utcnow(),
        )
        runner.set_capabilities(["ansible", "shell"])
        db.session.add(runner)

    runner.name = "{} local runner".format(hostname)[:120]
    runner.runner_uuid = runner_uuid
    runner.hostname = hostname
    runner.site = "local"
    runner.version = str(version or "")[:120]
    runner.running_steps = max(0, int(running_jobs or 0))
    try:
        load1, load5, _load15 = os.getloadavg()
        runner.load_average_1m = max(0.0, float(load1))
        runner.load_average_5m = max(0.0, float(load5))
    except (AttributeError, OSError):
        runner.load_average_1m = None
        runner.load_average_5m = None
    runner.cpu_count = os.cpu_count() or None
    runner.status_message = "Stopped" if stopped else str(status_message or "Ready")[:2000]
    runner.last_heartbeat_at = utcnow()
    db.session.commit()
    return runner
