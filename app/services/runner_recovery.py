"""Recover or terminate remote Jobs whose assigned runner has been lost."""

from datetime import datetime, timedelta, timezone

from app import db
from app.models import Job, JobStepExecutionSlice, Runner
from app.services.audit import record_audit_event
from app.services.runners import runner_health
from app.services.runner_slice_lifecycle import mark_lost_remote_slice


def utcnow():
    return datetime.now(timezone.utc)


def _mark_unfinished_steps(job, status, finished_at):
    for step in job.steps:
        if step.status in {"pending", "running"}:
            step.status = status
            if step.started_at is None and status == "failed":
                step.started_at = job.started_at
            step.finished_at = finished_at


def recover_lost_runner_jobs(now=None):
    """Handle Jobs assigned to runners whose heartbeat is offline.

    Queued-but-not-started Jobs are safe to return to the remote queue. Jobs
    that have started are never requeued automatically because the old runner
    may still be executing them after losing contact with the control plane.
    """

    now = now or utcnow()
    recovered = []
    failed = []
    cancelled = []
    slices_failed = []
    slices_cancelled = []

    offline_runner_ids = [
        runner.id
        for runner in Runner.query.filter_by(is_local=False).all()
        if runner_health(runner, now=now) == "offline"
    ]
    if not offline_runner_ids:
        return {
            "requeued": recovered,
            "failed": failed,
            "cancelled": cancelled,
            "slices_failed": slices_failed,
            "slices_cancelled": slices_cancelled,
        }

    execution_slices = (
        JobStepExecutionSlice.query
        .filter(
            JobStepExecutionSlice.dispatch_target == "remote",
            JobStepExecutionSlice.status.in_(("pending", "assigned", "running")),
            (
                JobStepExecutionSlice.required_runner_id.in_(offline_runner_ids)
                | JobStepExecutionSlice.assigned_runner_id.in_(offline_runner_ids)
            ),
        )
        .order_by(JobStepExecutionSlice.id.asc())
        .all()
    )

    for execution_slice in execution_slices:
        runner = execution_slice.assigned_runner or execution_slice.required_runner
        if runner is None or runner.id not in offline_runner_ids:
            continue
        job = execution_slice.step.job
        cancelling = job.status == "cancelling"
        changed, state = mark_lost_remote_slice(
            execution_slice,
            runner,
            cancelling=cancelling,
        )
        if not changed or state == "already_complete":
            continue

        if state == "cancelled":
            slices_cancelled.append(execution_slice.id)
        else:
            slices_failed.append(execution_slice.id)

        record_audit_event(
            "runner.execution_slice_terminated_after_loss",
            result=state,
            object_type="job_step_execution_slice",
            object_id=execution_slice.id,
            object_name="Job {} step {} slice {}".format(
                job.id,
                execution_slice.step.position,
                execution_slice.position,
            ),
            actor_username="system",
            authenticated_via="scheduler",
            details={
                "job_id": job.id,
                "job_step_id": execution_slice.job_step_id,
                "runner_id": runner.id,
                "runner_name": runner.name,
                "automatic_retry": False,
            },
        )

    jobs = (
        Job.query
        .filter(
            Job.dispatch_target == "remote",
            Job.assigned_runner_id.in_(offline_runner_ids),
            Job.status.in_(("queued", "running", "cancelling")),
        )
        .order_by(Job.id.asc())
        .all()
    )

    for job in jobs:
        runner = job.assigned_runner
        runner_name = runner.name if runner is not None else "unknown runner"

        if job.status == "queued":
            old_runner_id = job.assigned_runner_id
            job.assigned_runner_id = None
            job.assigned_at = None
            job.dispatch_token = ""
            job.message = (
                "Remote runner {} went offline before starting this Job; "
                "returned to the queue."
            ).format(runner_name)
            db.session.commit()
            record_audit_event(
                "runner.job_requeued_after_loss",
                result="requeued",
                object_type="job",
                object_id=job.id,
                object_name=job.project_name,
                actor_username="system",
                authenticated_via="scheduler",
                details={
                    "runner_id": old_runner_id,
                    "runner_name": runner_name,
                },
            )
            recovered.append(job.id)
            continue

        finished_at = now
        old_status = job.status
        if old_status == "cancelling":
            terminal_status = "cancelled"
            exit_code = None
            _mark_unfinished_steps(job, "cancelled", finished_at)
            cancelled.append(job.id)
        else:
            terminal_status = "failed"
            exit_code = 1
            _mark_unfinished_steps(job, "failed", finished_at)
            failed.append(job.id)

        job.status = terminal_status
        job.finished_at = finished_at
        job.exit_code = exit_code
        job.dispatch_token = ""
        job.message = (
            "Remote runner {} went offline while this Job was {}; "
            "the Job was not automatically retried."
        ).format(runner_name, old_status)
        db.session.commit()
        record_audit_event(
            "runner.job_terminated_after_loss",
            result=terminal_status,
            object_type="job",
            object_id=job.id,
            object_name=job.project_name,
            actor_username="system",
            authenticated_via="scheduler",
            details={
                "runner_id": job.assigned_runner_id,
                "runner_name": runner_name,
                "previous_status": old_status,
            },
        )

    return {
        "requeued": recovered,
        "failed": failed,
        "cancelled": cancelled,
        "slices_failed": slices_failed,
        "slices_cancelled": slices_cancelled,
    }


STALE_RUNNING_GRACE_SECONDS = 300


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def recover_stale_running_remote_work(now=None, grace_seconds=STALE_RUNNING_GRACE_SECONDS):
    """Fail remote work that the control plane still marks running after an
    online runner has repeatedly reported that it has no active assignments.

    This is intentionally conservative.  It only acts when the runner is
    healthy, reports ``running_steps == 0``, and has supplied a heartbeat at
    least ``grace_seconds`` after the work entered the running state.  The
    durable completion spool should normally reconcile within one heartbeat;
    this path is defence-in-depth for a completion record that is lost entirely.
    """

    now = now or utcnow()
    grace = timedelta(seconds=max(1, int(grace_seconds)))
    stale_slices = []
    stale_jobs = []

    runners = [
        runner
        for runner in Runner.query.filter_by(is_local=False).all()
        if runner_health(runner, now=now) == "healthy"
        and int(runner.running_steps or 0) == 0
        and runner.last_heartbeat_at is not None
    ]

    for runner in runners:
        heartbeat = _as_utc(runner.last_heartbeat_at)

        slices = (
            JobStepExecutionSlice.query
            .filter(
                JobStepExecutionSlice.dispatch_target == "remote",
                JobStepExecutionSlice.assigned_runner_id == runner.id,
                JobStepExecutionSlice.status == "running",
            )
            .order_by(JobStepExecutionSlice.id.asc())
            .all()
        )
        for execution_slice in slices:
            started = _as_utc(execution_slice.started_at or execution_slice.assigned_at)
            if started is None or heartbeat < started + grace:
                continue
            job = execution_slice.step.job
            cancelling = job.status == "cancelling"
            changed, state = mark_lost_remote_slice(
                execution_slice,
                runner,
                cancelling=cancelling,
                loss_reason=(
                    "is online but has reported no active assignments for at "
                    "least {} seconds".format(int(grace.total_seconds()))
                ),
            )
            if not changed or state == "already_complete":
                continue
            stale_slices.append(execution_slice.id)
            record_audit_event(
                "runner.execution_slice_reconciled_stale_running",
                result=state,
                object_type="job_step_execution_slice",
                object_id=execution_slice.id,
                object_name="Job {} step {} slice {}".format(
                    job.id, execution_slice.step.position, execution_slice.position
                ),
                actor_username="system",
                authenticated_via="scheduler",
                details={
                    "job_id": job.id,
                    "runner_id": runner.id,
                    "runner_name": runner.name,
                    "runner_running_steps": int(runner.running_steps or 0),
                    "grace_seconds": int(grace.total_seconds()),
                },
            )

        # Compatibility for pre-slice remote Jobs. Slice-based Jobs are handled
        # above so their normal aggregation/dependency logic remains authoritative.
        jobs = (
            Job.query
            .filter(
                Job.dispatch_target == "remote",
                Job.assigned_runner_id == runner.id,
                Job.status.in_(("running", "cancelling")),
            )
            .order_by(Job.id.asc())
            .all()
        )
        for job in jobs:
            if any(step.execution_slices for step in job.steps):
                continue
            started = _as_utc(job.started_at or job.assigned_at)
            if started is None or heartbeat < started + grace:
                continue
            previous_status = job.status
            finished_at = now
            if previous_status == "cancelling":
                terminal_status = "cancelled"
                exit_code = None
                _mark_unfinished_steps(job, "cancelled", finished_at)
            else:
                terminal_status = "failed"
                exit_code = 1
                _mark_unfinished_steps(job, "failed", finished_at)
            job.status = terminal_status
            job.finished_at = finished_at
            job.exit_code = exit_code
            job.dispatch_token = ""
            job.message = (
                "Remote runner {} is online but reported no active assignments "
                "for at least {} seconds; stale-running state was reconciled."
            ).format(runner.name, int(grace.total_seconds()))
            db.session.commit()
            stale_jobs.append(job.id)
            record_audit_event(
                "runner.job_reconciled_stale_running",
                result=terminal_status,
                object_type="job",
                object_id=job.id,
                object_name=job.project_name,
                actor_username="system",
                authenticated_via="scheduler",
                details={
                    "runner_id": runner.id,
                    "runner_name": runner.name,
                    "previous_status": previous_status,
                    "runner_running_steps": int(runner.running_steps or 0),
                    "grace_seconds": int(grace.total_seconds()),
                },
            )

    return {"jobs": stale_jobs, "slices": stale_slices}
