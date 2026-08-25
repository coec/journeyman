"""Recover or terminate remote Jobs whose assigned runner has been lost."""

from datetime import datetime, timezone

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
