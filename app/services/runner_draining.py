"""Runner draining for disruptive runner-management Jobs."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import object_session

from app import db
from app.models import Job, JobStepExecutionSlice, Runner, RunnerEnvironmentSync


DRAINING_MANAGEMENT_ACTIONS = {"update", "unregister", "delete"}
TERMINAL_JOB_STATUSES = {"successful", "failed", "cancelled"}


def utcnow():
    return datetime.now(timezone.utc)


def _management_values(job):
    """Return (action, runner_reference) from a Manage Remote Runner Job."""
    snapshot = getattr(job, "package_snapshot", None)
    if snapshot is None:
        return "", ""
    try:
        values = snapshot.get_display_values()
    except (TypeError, ValueError):
        return "", ""
    action = ""
    runner_name = ""
    runner_host = ""
    for item in values:
        if not isinstance(item, dict):
            continue
        variable = str(item.get("variable_name") or "")
        value = str(item.get("value") or "").strip()
        if variable == "journeyman_manage_action":
            action = value.lower()[:32]
        elif variable == "journeyman_runner_name":
            runner_name = value
        elif variable == "journeyman_runner_host":
            runner_host = value
    return action, (runner_name or runner_host)


def management_job_target(job):
    action, reference = _management_values(job)
    if action not in DRAINING_MANAGEMENT_ACTIONS or not reference:
        return action, None
    from app.services.runners import find_runner_for_management, RunnerRemovalError
    try:
        return action, find_runner_for_management(reference)
    except RunnerRemovalError:
        # Update/unregister/delete validation in the playbook/service remains the
        # source of the user-facing failure.  A missing target cannot be drained.
        return action, None


def _unassign_not_started_work(runner):
    """Return assignments that have not started to their queues.

    Establishing the drain and revoking unstarted assignments in one database
    transaction closes the normal claim race: a remote runner may keep reporting
    in-flight work, but it cannot acquire anything new once drain_job_id is set.
    """
    Job.query.filter(
        Job.assigned_runner_id == runner.id,
        Job.dispatch_target == "remote",
        Job.status == "queued",
    ).update(
        {
            Job.assigned_runner_id: None,
            Job.assigned_at: None,
            Job.dispatch_token: "",
            Job.message: "Returned to the queue because the runner is draining.",
        },
        synchronize_session=False,
    )

    JobStepExecutionSlice.query.filter(
        JobStepExecutionSlice.assigned_runner_id == runner.id,
        JobStepExecutionSlice.status == "assigned",
    ).update(
        {
            JobStepExecutionSlice.assigned_runner_id: None,
            JobStepExecutionSlice.assigned_at: None,
            JobStepExecutionSlice.dispatch_token: "",
            JobStepExecutionSlice.status: "pending",
            JobStepExecutionSlice.message: "Returned to the queue because the runner is draining.",
        },
        synchronize_session=False,
    )


def request_runner_drain(runner, job, *, action):
    if runner is None or runner.is_local:
        return False

    # Serialize drain acquisition against remote Job/slice claims. Claim
    # paths lock this same row immediately before assigning work.
    locked_runner = (
        db.session.execute(
            select(Runner)
            .where(Runner.id == runner.id)
            .with_for_update()
        )
        .unique()
        .scalar_one_or_none()
    )
    if locked_runner is None or locked_runner.is_local:
        return False

    if locked_runner.drain_job_id not in (None, job.id):
        return False
    if locked_runner.drain_job_id is None:
        locked_runner.drain_job_id = job.id
        locked_runner.drain_requested_at = utcnow()
        locked_runner.drain_reason = "Runner management: {} (Job #{})".format(
            action, job.id
        )[:255]
    _unassign_not_started_work(locked_runner)
    return True


def runner_has_active_work(runner):
    """Return whether Journeyman still has work executing on ``runner``."""
    whole_jobs = Job.query.filter(
        Job.assigned_runner_id == runner.id,
        Job.status.in_(("running", "cancelling")),
    ).count()
    if whole_jobs:
        return True
    slices = JobStepExecutionSlice.query.filter(
        JobStepExecutionSlice.assigned_runner_id == runner.id,
        JobStepExecutionSlice.status == "running",
    ).count()
    if slices:
        return True
    environment_syncs = RunnerEnvironmentSync.query.filter(
        RunnerEnvironmentSync.runner_id == runner.id,
        RunnerEnvironmentSync.status == "building",
    ).count()
    if environment_syncs:
        return True
    # The heartbeat is an additional conservative signal.  It protects against
    # a worker that is still executing while its DB lifecycle update is delayed.
    return int(runner.running_steps or 0) > 0


def management_job_ready_to_start(job):
    """Establish a drain and return whether a local management Job may start."""
    action, runner = management_job_target(job)
    if action not in DRAINING_MANAGEMENT_ACTIONS or runner is None:
        return True

    if not request_runner_drain(runner, job, action=action):
        job.message = (
            "Waiting: target runner {} is already draining for another management Job."
            .format(runner.name)
        )
        db.session.commit()
        return False

    if runner_has_active_work(runner):
        job.message = "Waiting for target runner {} to drain.".format(runner.name)
        db.session.commit()
        return False

    job.message = "Target runner {} drained; management operation may start.".format(runner.name)
    db.session.commit()
    return True


def release_runner_drain_for_job(job):
    """Release any runner drain owned by a terminal/cancelled management Job."""
    session = object_session(job)
    if session is None or getattr(job, "id", None) is None:
        return False
    with session.no_autoflush:
        runner = session.query(Runner).filter(Runner.drain_job_id == job.id).one_or_none()
    if runner is None:
        return False
    runner.drain_job_id = None
    runner.drain_requested_at = None
    runner.drain_reason = ""
    return True
