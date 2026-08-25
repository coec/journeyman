"""Remote-runner Job lifecycle validation and state transitions."""

import secrets
from datetime import datetime, timezone

from app import db
from app.models import Job, JobStepHostResult
from app.models.reaction import sync_reaction_for_job


FINAL_JOB_STATUSES = {"successful", "failed", "cancelled"}
MAX_MESSAGE_LENGTH = 4000
MAX_STEP_OUTPUT_LENGTH = 2 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc)


def assignment_matches(job, runner, token):
    return bool(
        job
        and runner
        and job.dispatch_target == "remote"
        and job.assigned_runner_id == runner.id
        and job.dispatch_token
        and token
        and secrets.compare_digest(job.dispatch_token, token)
    )


def start_remote_job(job, runner, token):
    """Atomically acknowledge an assignment and mark the Job running."""
    if not assignment_matches(job, runner, token):
        return False, "assignment_mismatch"

    started_at = utcnow()
    updated = (
        Job.query
        .filter(
            Job.id == job.id,
            Job.status == "queued",
            Job.dispatch_target == "remote",
            Job.assigned_runner_id == runner.id,
            Job.dispatch_token == token,
        )
        .update(
            {
                Job.status: "running",
                Job.started_at: started_at,
                Job.finished_at: None,
                Job.exit_code: None,
                Job.message: "Running on remote runner {}.".format(runner.name),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.session.rollback()
        current = db.session.get(Job, job.id)
        if assignment_matches(current, runner, token) and current.status in {
            "running", "cancelling"
        }:
            return True, "already_started"
        return False, "invalid_state"

    # Query.update bypasses SQLAlchemy attribute listeners, so explicitly keep
    # a linked Reaction synchronized with the remote Job's running state.
    sync_reaction_for_job(job, status="running")
    db.session.commit()
    return True, "started"


def remote_job_control(job, runner, token):
    if not assignment_matches(job, runner, token):
        return None
    return {
        "job_id": job.id,
        "status": job.status,
        "cancel_requested": job.status == "cancelling",
    }


def _trim_output(value):
    text = str(value or "")
    if len(text) <= MAX_STEP_OUTPUT_LENGTH:
        return text
    marker = "\n[output truncated by Journeyman]\n"
    return text[: MAX_STEP_OUTPUT_LENGTH - len(marker)] + marker


def complete_remote_job(job, runner, token, payload):
    """Record terminal Job and per-step results supplied by the assigned runner."""
    if not assignment_matches(job, runner, token):
        return False, "assignment_mismatch"

    requested_status = str(payload.get("status") or "").strip().lower()
    if requested_status not in FINAL_JOB_STATUSES:
        return False, "invalid_status"
    if job.status not in {"running", "cancelling"}:
        if job.status == requested_status:
            return True, "already_complete"
        return False, "invalid_state"
    if job.status == "cancelling" and requested_status == "successful":
        return False, "cancel_pending"

    try:
        exit_code = payload.get("exit_code")
        exit_code = None if exit_code is None else int(exit_code)
    except (TypeError, ValueError):
        return False, "invalid_exit_code"

    step_by_position = {step.position: step for step in job.steps}
    step_results = payload.get("steps") or []
    if not isinstance(step_results, list):
        return False, "invalid_steps"

    for result in step_results:
        if not isinstance(result, dict):
            return False, "invalid_steps"
        try:
            position = int(result.get("position"))
        except (TypeError, ValueError):
            return False, "invalid_steps"
        step = step_by_position.get(position)
        if step is None:
            return False, "unknown_step"
        step_status = str(result.get("status") or "").strip().lower()
        if step_status not in FINAL_JOB_STATUSES | {"skipped"}:
            return False, "invalid_step_status"
        try:
            step_exit_code = result.get("exit_code")
            step_exit_code = None if step_exit_code is None else int(step_exit_code)
        except (TypeError, ValueError):
            return False, "invalid_step_exit_code"

        host_results = result.get("host_results") or []
        if not isinstance(host_results, list):
            return False, "invalid_host_results"

        parsed_host_results = []
        seen_hosts = set()
        for host_result in host_results:
            if not isinstance(host_result, dict):
                return False, "invalid_host_results"
            host = str(host_result.get("host") or "").strip()
            if not host or len(host) > 255 or host in seen_hosts:
                return False, "invalid_host_results"
            seen_hosts.add(host)
            host_status = str(host_result.get("status") or "").strip().lower()
            if host_status not in {"successful", "failed", "unreachable"}:
                return False, "invalid_host_status"
            try:
                host_exit_code = host_result.get("exit_code")
                host_exit_code = (
                    None if host_exit_code is None else int(host_exit_code)
                )
            except (TypeError, ValueError):
                return False, "invalid_host_exit_code"
            parsed_host_results.append(
                {
                    "host": host,
                    "status": host_status,
                    "exit_code": host_exit_code,
                    "stdout": _trim_output(host_result.get("stdout")),
                    "stderr": _trim_output(host_result.get("stderr")),
                }
            )

        step.host_results[:] = []
        step.host_results.extend(
            JobStepHostResult(step=step, **host_result)
            for host_result in parsed_host_results
        )
        step.status = step_status
        step.exit_code = step_exit_code
        step.command = str(result.get("command") or "")[:4000]
        step.stdout = _trim_output(result.get("stdout"))
        step.stderr = _trim_output(result.get("stderr"))
        if step.started_at is None:
            step.started_at = job.started_at or utcnow()
        step.finished_at = utcnow()

    finished_at = utcnow()
    for step in job.steps:
        if step.status == "pending":
            step.status = "cancelled" if requested_status == "cancelled" else "skipped"
            step.finished_at = finished_at

    job.status = requested_status
    job.finished_at = finished_at
    job.exit_code = exit_code
    job.message = str(payload.get("message") or "")[:MAX_MESSAGE_LENGTH]
    if not job.message:
        job.message = "Remote runner reported Job {}.".format(requested_status)
    job.dispatch_token = ""
    db.session.commit()
    return True, "completed"
