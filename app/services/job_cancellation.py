"""Shared Job cancellation service for web and API callers."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app

from app import db
from app.services.audit import record_audit_event
from app.services.runners import runner_health
from app.services.runner_draining import release_runner_drain_for_job


@dataclass(frozen=True)
class JobCancellationResult:
    changed: bool
    status: str
    message: str


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cancel_job(job, *, source="Journeyman web interface"):
    """Request cancellation of a Job and return a caller-neutral result."""

    if job.status in {"queued", "waiting_oversight"}:
        previous_status = job.status
        was_oversight = previous_status == "waiting_oversight"
        now = _utcnow()
        job.status = "cancelled"
        job.cancel_requested_at = now
        job.finished_at = now
        job.message = (
            "Stopped while waiting for oversight."
            if was_oversight
            else "Cancelled before execution started."
        )

        for step in job.steps:
            if step.status == "pending":
                step.status = "cancelled"
                step.finished_at = now
            for execution_slice in step.execution_slices:
                if execution_slice.status in {"pending", "assigned"}:
                    execution_slice.status = "cancelled"
                    execution_slice.finished_at = now
                    execution_slice.dispatch_token = ""

        # A disruptive runner-management Job may establish its drain while
        # still queued. Cancelling it is terminal, so release that ownership
        # in the same transaction as the Job cancellation.
        release_runner_drain_for_job(job)
        db.session.commit()
        record_audit_event(
            "job.cancel",
            object_type="job",
            object_id=job.id,
            object_name=job.project_name,
            details={"previous_status": previous_status, "new_status": "cancelled", "source": source},
        )
        return JobCancellationResult(True, "cancelled", "Job #{} cancelled.".format(job.id))

    if job.status == "running":
        previous_status = job.status
        now = _utcnow()
        job.status = "cancelling"
        job.cancel_requested_at = now
        job.message = "Stop requested from {}.".format(source)
        for step in job.steps:
            for execution_slice in step.execution_slices:
                if execution_slice.status == "pending":
                    execution_slice.status = "cancelled"
                    execution_slice.finished_at = now
            if (
                step.status == "pending"
                and step.execution_slices
                and all(
                    execution_slice.status == "cancelled"
                    for execution_slice in step.execution_slices
                )
            ):
                step.status = "cancelled"
                step.finished_at = now

        db.session.commit()
        record_audit_event(
            "job.cancel",
            object_type="job",
            object_id=job.id,
            object_name=job.project_name,
            details={"previous_status": previous_status, "new_status": "cancelling", "source": source},
        )
        return JobCancellationResult(True, "cancelling", "Stop requested for Job #{}.".format(job.id))

    if job.status == "cancelling":
        if job.cancel_requested_at is None:
            job.cancel_requested_at = _utcnow()
            db.session.commit()
        return JobCancellationResult(False, "cancelling", "Job #{} is already stopping.".format(job.id))

    return JobCancellationResult(False, job.status, "Job #{} is already {}.".format(job.id, job.status))


_TERMINAL_STEP_STATUSES = {"successful", "failed", "cancelled", "blocked", "skipped"}
_TERMINAL_SLICE_STATUSES = {"successful", "failed", "cancelled"}


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _runner_reports_active_work(runner, *, now):
    if runner is None:
        return False
    if runner_health(runner, now=now) == "offline":
        return False
    return int(runner.running_steps or 0) > 0


def _job_may_still_be_executing(job, *, now):
    """Conservatively decide whether a cancelling Job may still have live work."""

    if job.dispatch_target == "sliced":
        for step in job.steps:
            for execution_slice in step.execution_slices:
                if execution_slice.status not in {"assigned", "running"}:
                    continue
                runner = execution_slice.assigned_runner or execution_slice.required_runner
                if _runner_reports_active_work(runner, now=now):
                    return True
        return False

    if job.dispatch_target == "local":
        from app.models import Runner
        runner = Runner.query.filter_by(is_local=True).one_or_none()
        return _runner_reports_active_work(runner, now=now)

    runner = job.assigned_runner or job.required_runner
    return _runner_reports_active_work(runner, now=now)


def _finalize_abandoned_cancellation(job, *, now, stale_seconds):
    for step in job.steps:
        for execution_slice in step.execution_slices:
            if execution_slice.status not in _TERMINAL_SLICE_STATUSES:
                execution_slice.status = "cancelled"
                execution_slice.finished_at = now
                execution_slice.exit_code = None
                execution_slice.dispatch_token = ""
                execution_slice.message = (
                    "Cancellation finalized after no runner reported active execution."
                )
        if step.status not in _TERMINAL_STEP_STATUSES:
            step.status = "cancelled"
            step.finished_at = now
            step.exit_code = None

    job.status = "cancelled"
    job.finished_at = now
    job.exit_code = None
    job.dispatch_token = ""
    job.message = (
        "Cancellation finalized after the Job remained in stopping state for "
        "at least {} seconds and no runner reported active execution."
    ).format(stale_seconds)
    # Recovery is also a terminal transition. Do not leave a runner drain
    # orphaned if its management Job was abandoned while cancelling.
    release_runner_drain_for_job(job)


def recover_stale_cancelling_jobs(*, jobs=None, now=None, stale_seconds=None):
    """Finalize abandoned cancellation state without terminating live work.

    A cancelling Job is eligible only after a grace period and only when its
    relevant runner(s) report no active execution.  This lets historical or
    orphaned cancellation records converge to a terminal state while keeping
    deletion and retention code from treating ordinary cancellation as safe to
    destroy prematurely.
    """

    now = _aware(now or datetime.now(timezone.utc))
    if stale_seconds is None:
        stale_seconds = int(current_app.config.get("CANCELLATION_STALE_SECONDS", 300))
    stale_seconds = max(60, int(stale_seconds))
    cutoff = now - timedelta(seconds=stale_seconds)

    if jobs is None:
        from app.models import Job
        candidates = (
            Job.query
            .filter(
                Job.status == "cancelling",
                Job.cancel_requested_at.isnot(None),
                Job.cancel_requested_at <= cutoff,
            )
            .order_by(Job.id.asc())
            .all()
        )
    else:
        candidates = [
            job for job in jobs
            if job.status == "cancelling"
            and _aware(job.cancel_requested_at) is not None
            and _aware(job.cancel_requested_at) <= cutoff
        ]

    recovered = []
    for job in candidates:
        if _job_may_still_be_executing(job, now=now):
            continue
        _finalize_abandoned_cancellation(
            job, now=now, stale_seconds=stale_seconds
        )
        db.session.commit()
        record_audit_event(
            "job.cancel_recovered",
            result="cancelled",
            object_type="job",
            object_id=job.id,
            object_name=job.project_name,
            actor_username="system",
            authenticated_via="scheduler",
            details={
                "cancel_requested_at": (
                    job.cancel_requested_at.isoformat()
                    if job.cancel_requested_at is not None else None
                ),
                "stale_seconds": stale_seconds,
            },
        )
        recovered.append(job.id)

    return recovered
