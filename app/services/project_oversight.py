"""Execution-time oversight gates for multi-step Project Jobs."""

from app import db


TERMINAL_STEP_STATUSES = {
    "successful",
    "failed",
    "cancelled",
    "blocked",
    "skipped",
}


def dependency_state(step, steps_by_position):
    """Return the workflow dependency state for one JobStep."""

    dependencies = [
        steps_by_position.get(position)
        for position in step.get_dependency_positions()
    ]

    if any(item is None for item in dependencies):
        return "blocked"

    if not dependencies:
        return "skipped" if step.failure_only else "eligible"

    if any(item.status not in TERMINAL_STEP_STATUSES for item in dependencies):
        return "waiting"

    statuses = [item.status for item in dependencies]

    if step.failure_only:
        return "eligible" if "failed" in statuses else "skipped"

    return "eligible" if all(value == "successful" for value in statuses) else "blocked"


def oversight_candidates(job):
    """Return runnable pending steps that still require reviewer approval."""

    if not getattr(job, "oversight_required_between_all_steps", False):
        return []

    steps_by_position = {
        step.position: step
        for step in job.steps
    }

    return [
        step
        for step in sorted(job.steps, key=lambda item: item.position)
        if (
            step.status == "pending"
            and getattr(step, "oversight_required_before", False)
            and not step.oversight_approved
            and dependency_state(step, steps_by_position) == "eligible"
        )
    ]


def update_job_oversight_state(job):
    """Pause a Job when its next runnable workflow batch needs oversight."""

    if not getattr(job, "oversight_required_between_all_steps", False):
        return False

    if job.status in {"successful", "failed", "cancelled", "cancelling"}:
        return False

    # Do not change the Job status while an already-authorised batch is still
    # executing. Oversight is presented between execution batches.
    if any(step.status == "running" for step in job.steps):
        return False

    candidates = oversight_candidates(job)
    if not candidates:
        return False

    job.status = "waiting_oversight"
    job.message = "Oversight required before step(s) {}.".format(
        ", ".join(str(step.position) for step in candidates)
    )
    from app.services.notifications import queue_oversight_required
    queue_oversight_required(job, candidates)
    db.session.flush()
    return True


def approve_current_oversight(job):
    """Approve the currently runnable batch and make the Job dispatchable."""

    if job.status != "waiting_oversight":
        return []

    candidates = oversight_candidates(job)
    if not candidates:
        return []

    for step in candidates:
        step.oversight_approved = True

    # Direct Jobs are claimed only from queued. Oversight Jobs are currently
    # forced through execution slices, but retain this conservative distinction
    # for compatibility with historical/direct dispatch modes.
    if job.dispatch_target == "local":
        job.status = "queued"
    else:
        job.status = "running" if job.started_at is not None else "queued"

    job.message = "Oversight completed; step(s) {} may proceed.".format(
        ", ".join(str(step.position) for step in candidates)
    )
    db.session.flush()
    return candidates
