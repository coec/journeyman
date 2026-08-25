"""Lifecycle and aggregation for remotely executed Job-step slices."""

import secrets
from datetime import datetime, timezone

from app import db
from app.models import JobStepHostResult

FINAL_SLICE_STATUSES = {"successful", "failed", "cancelled"}
TERMINAL_STEP_STATUSES = {"successful", "failed", "cancelled", "blocked", "skipped"}
MAX_OUTPUT_LENGTH = 2 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc)


def assignment_matches(execution_slice, runner, token):
    return bool(
        execution_slice
        and runner
        and execution_slice.dispatch_target == "remote"
        and execution_slice.assigned_runner_id == runner.id
        and execution_slice.dispatch_token
        and token
        and secrets.compare_digest(execution_slice.dispatch_token, token)
    )


def start_remote_slice(execution_slice, runner, token):
    if not assignment_matches(execution_slice, runner, token):
        return False, "assignment_mismatch"
    if execution_slice.status == "running":
        return True, "already_started"
    if execution_slice.status != "assigned":
        return False, "invalid_state"
    execution_slice.status = "running"
    execution_slice.started_at = utcnow()
    execution_slice.finished_at = None
    execution_slice.message = "Running on remote runner {}.".format(runner.name)
    db.session.commit()
    return True, "started"


def remote_slice_control(execution_slice, runner, token):
    if not assignment_matches(execution_slice, runner, token):
        return None
    return {
        "job_id": execution_slice.step.job_id,
        "slice_id": execution_slice.id,
        "status": execution_slice.status,
        "cancel_requested": execution_slice.step.job.status == "cancelling",
    }


def update_remote_slice_output(execution_slice, runner, token, payload):
    """Persist a live output snapshot reported by the assigned remote runner."""

    if not assignment_matches(execution_slice, runner, token):
        return False, "assignment_mismatch"
    if execution_slice.status != "running":
        return False, "invalid_state"

    payload = payload if isinstance(payload, dict) else {}
    execution_slice.command = str(payload.get("command") or "")[:4000]
    execution_slice.stdout = _trim(payload.get("stdout"))
    execution_slice.stderr = _trim(payload.get("stderr"))
    db.session.commit()
    return True, "updated"


def update_local_slice_output(execution_slice, *, command="", stdout="", stderr=""):
    """Persist a live output snapshot produced by the built-in local runner."""

    if execution_slice is None or execution_slice.dispatch_target != "local":
        return False
    if execution_slice.status != "running":
        return False

    execution_slice.command = str(command or "")[:4000]
    execution_slice.stdout = _trim(stdout)
    execution_slice.stderr = _trim(stderr)
    db.session.commit()
    return True


def start_local_slice(execution_slice, runner):
    if execution_slice is None or execution_slice.dispatch_target != "local":
        return False, "invalid_slice"
    if execution_slice.status == "running":
        return True, "already_started"
    if execution_slice.status != "pending":
        return False, "invalid_state"
    now = utcnow()
    execution_slice.status = "running"
    execution_slice.assigned_runner_id = getattr(runner, "id", None)
    execution_slice.assigned_at = now
    execution_slice.started_at = now
    execution_slice.finished_at = None
    execution_slice.runner_name = str(getattr(runner, "name", "") or "local")
    execution_slice.runner_hostname = str(getattr(runner, "hostname", "") or "localhost")
    execution_slice.message = "Running on built-in local runner {}.".format(
        execution_slice.runner_hostname or execution_slice.runner_name
    )
    step = execution_slice.step
    job = step.job
    if job.status == "queued":
        job.status = "running"
        job.started_at = now
        job.finished_at = None
        job.exit_code = None
        job.message = "Executing using per-host runner slices."
    if step.status == "pending":
        step.status = "running"
        step.started_at = now
        step.finished_at = None
        step.exit_code = None
    db.session.commit()
    return True, "started"


def local_slice_control(execution_slice):
    if execution_slice is None or execution_slice.dispatch_target != "local":
        return None
    return {
        "job_id": execution_slice.step.job_id,
        "slice_id": execution_slice.id,
        "status": execution_slice.status,
        "cancel_requested": execution_slice.step.job.status == "cancelling",
    }


def _trim(value):
    text = str(value or "")
    if len(text) <= MAX_OUTPUT_LENGTH:
        return text
    marker = "\n[output truncated by Journeyman]\n"
    return text[: MAX_OUTPUT_LENGTH - len(marker)] + marker


def _has_failure_branch(step, steps):
    return any(
        candidate.failure_only
        and step.position in candidate.get_dependency_positions()
        for candidate in steps
    )


def _aggregate_step(step):
    slices = list(step.execution_slices)
    if not slices or any(item.status not in FINAL_SLICE_STATUSES for item in slices):
        return False
    now = utcnow()
    if any(item.status == "failed" for item in slices):
        step.status = "failed"
        step.exit_code = next((item.exit_code for item in slices if item.status == "failed" and item.exit_code is not None), 1)
    elif any(item.status == "cancelled" for item in slices):
        step.status = "cancelled"
        step.exit_code = None
    else:
        step.status = "successful"
        step.exit_code = 0
    step.finished_at = now
    step.command = "\n".join(item.command for item in slices if item.command)
    step.stdout = "\n".join(
        "[runner {}]\n{}".format(item.runner_hostname or item.runner_name or "remote", item.stdout)
        for item in slices if item.stdout
    )
    step.stderr = "\n".join(
        "[runner {}]\n{}".format(item.runner_hostname or item.runner_name or "remote", item.stderr)
        for item in slices if item.stderr
    )
    merged_stats = {}
    for item in slices:
        merged_stats.update(item.get_custom_stats())
    step.set_custom_stats(merged_stats)
    return True


def _reconcile_dependencies(job):
    from app.services.runner_slice_dispatch import reconcile_non_runnable_steps
    from app.services.project_oversight import update_job_oversight_state
    reconcile_non_runnable_steps(job)
    update_job_oversight_state(job)


def _finalize_job_if_complete(job):
    if any(step.status not in TERMINAL_STEP_STATUSES for step in job.steps):
        return False
    now = utcnow()
    if job.status == "cancelling" or any(step.status == "cancelled" for step in job.steps):
        job.status = "cancelled"
        job.exit_code = None
        job.message = "Project execution was cancelled."
    else:
        failed = [step for step in job.steps if step.status == "failed"]
        unhandled = [step for step in failed if not _has_failure_branch(step, job.steps)]
        blocked = [step for step in job.steps if step.status == "blocked"]
        if unhandled or blocked:
            job.status = "failed"
            job.exit_code = next((step.exit_code for step in reversed(unhandled) if step.exit_code is not None), 1)
            job.message = "Project execution completed with {} unhandled failed and {} blocked step(s).".format(len(unhandled), len(blocked))
        else:
            job.status = "successful"
            job.exit_code = 0
            job.message = "Project execution completed successfully."
    job.finished_at = now
    return True


def fail_pending_remote_slice(execution_slice, message):
    """Fail an unclaimed remote slice that can no longer satisfy its snapshot."""

    if execution_slice is None or execution_slice.status != "pending":
        return False

    now = utcnow()
    execution_slice.status = "failed"
    execution_slice.finished_at = now
    execution_slice.exit_code = 1
    execution_slice.message = str(message or "")[:4000]
    execution_slice.dispatch_token = ""

    step = execution_slice.step
    job = step.job
    _aggregate_step(step)
    _reconcile_dependencies(job)
    _finalize_job_if_complete(job)
    db.session.commit()
    return True


def _complete_slice(execution_slice, payload, *, runner=None, local=False):
    requested_status = str(payload.get("status") or "").strip().lower()
    if requested_status not in FINAL_SLICE_STATUSES:
        return False, "invalid_status"
    valid_states = {"running"} if local else {"assigned", "running"}
    if execution_slice.status not in valid_states:
        if execution_slice.status == requested_status:
            return True, "already_complete"
        return False, "invalid_state"
    if execution_slice.step.job.status == "cancelling" and requested_status == "successful":
        requested_status = "cancelled"

    try:
        exit_code = payload.get("exit_code")
        exit_code = None if exit_code is None else int(exit_code)
    except (TypeError, ValueError):
        return False, "invalid_exit_code"

    step_results = payload.get("steps") or []
    result = step_results[0] if isinstance(step_results, list) and step_results else {}
    host_results = result.get("host_results") or [] if isinstance(result, dict) else []
    if not isinstance(host_results, list):
        return False, "invalid_host_results"

    parsed = []
    allowed_hosts = set(execution_slice.get_hosts())
    seen = set()
    for item in host_results:
        if not isinstance(item, dict):
            return False, "invalid_host_results"
        host = str(item.get("host") or "").strip()
        if not host or host in seen or (allowed_hosts and host not in allowed_hosts):
            return False, "invalid_host_results"
        seen.add(host)
        status = str(item.get("status") or "").strip().lower()
        if status not in {"successful", "failed", "unreachable"}:
            return False, "invalid_host_status"
        parsed.append((host, status, item))

    # Preserve compatibility with historical or runner-error completions that
    # do not contain structured per-host callback data by synthesising one host
    # row per slice host from the slice-level result.
    if not parsed and allowed_hosts:
        synthetic_status = (
            "successful" if requested_status == "successful"
            else "failed" if requested_status == "failed"
            else "cancelled"
        )
        parsed = [
            (host, synthetic_status, {"exit_code": exit_code, "stdout": "", "stderr": ""})
            for host in sorted(allowed_hosts)
        ]

    execution_slice.status = requested_status
    execution_slice.finished_at = utcnow()
    execution_slice.exit_code = exit_code
    execution_slice.message = str(payload.get("message") or "")[:4000]
    if isinstance(result, dict):
        execution_slice.command = str(result.get("command") or "")[:4000]
        execution_slice.stdout = _trim(result.get("stdout"))
        execution_slice.stderr = _trim(result.get("stderr"))
    execution_slice.dispatch_token = ""

    step = execution_slice.step
    existing_by_host = {item.host: item for item in step.host_results}
    for host, status, item in parsed:
        row = existing_by_host.get(host)
        if row is None:
            row = JobStepHostResult(step=step, host=host, status=status)
            step.host_results.append(row)
            existing_by_host[host] = row
        row.status = status
        try:
            value = item.get("exit_code")
            row.exit_code = None if value is None else int(value)
        except (TypeError, ValueError):
            return False, "invalid_host_exit_code"
        row.stdout = _trim(item.get("stdout"))
        row.stderr = _trim(item.get("stderr"))
        row.runner_id = execution_slice.assigned_runner_id
        row.runner_name = execution_slice.runner_name or str(getattr(runner, "name", "") or "")
        row.runner_hostname = execution_slice.runner_hostname or str(getattr(runner, "hostname", "") or "")
        row.runner_local = bool(local)

    step_completed = _aggregate_step(step)
    job = step.job

    if (
        step_completed
        and step.status == "successful"
        and step.refresh_inventory_after
        and job.dispatch_target == "sliced"
    ):
        # Do not expose this dependency as successful until refresh/replanning
        # has finished. A separate local/remote runner may otherwise see the
        # successful dependency, claim a descendant slice, and race the
        # replanner. Keep the completed step logically running while its
        # post-step inventory refresh is in progress; all of its execution
        # slices are already terminal so it cannot be dispatched again.
        step.status = "running"
        step.finished_at = None
        db.session.commit()

        from app.services.job_inventory_refresh import (
            JobInventoryRefreshError,
            refresh_job_inventories_after_step,
        )

        try:
            refreshed = refresh_job_inventories_after_step(
                job,
                step,
            )
            step.status = "successful"
            step.exit_code = 0
            step.finished_at = utcnow()
            if refreshed:
                step.stdout += (
                    "\nJourneyman refreshed and replanned {} dependent "
                    "inventory snapshot(s).\n"
                ).format(len(refreshed))
        except JobInventoryRefreshError as exc:
            step.status = "failed"
            step.exit_code = 1
            step.finished_at = utcnow()
            step.stderr += (
                "\nJourneyman inventory refresh/replan failed: {}\n"
                .format(exc)
            )

    _reconcile_dependencies(job)
    _finalize_job_if_complete(job)
    db.session.commit()
    return True, "completed"


def complete_remote_slice(execution_slice, runner, token, payload):
    if not assignment_matches(execution_slice, runner, token):
        return False, "assignment_mismatch"
    return _complete_slice(execution_slice, payload, runner=runner, local=False)


def complete_local_slice(execution_slice, runner, payload):
    if execution_slice is None or execution_slice.dispatch_target != "local":
        return False, "invalid_slice"
    if execution_slice.assigned_runner_id not in {None, getattr(runner, "id", None)}:
        return False, "assignment_mismatch"
    return _complete_slice(execution_slice, payload, runner=runner, local=True)

def mark_lost_remote_slice(execution_slice, runner, *, cancelling=False):
    """Fail one remote slice after its required runner is lost.

    A lost slice is never reassigned automatically.  Other slices belonging
    to the same JobStep are left alone and may finish normally; normal slice
    aggregation then decides the JobStep and workflow result.
    """
    if execution_slice is None or execution_slice.dispatch_target != "remote":
        return False, "invalid_slice"
    if runner is None:
        return False, "invalid_runner"
    if execution_slice.status in FINAL_SLICE_STATUSES:
        return True, "already_complete"
    if execution_slice.status not in {"pending", "assigned", "running"}:
        return False, "invalid_state"

    runner_ids = {
        value
        for value in (
            execution_slice.required_runner_id,
            execution_slice.assigned_runner_id,
        )
        if value is not None
    }
    if runner.id not in runner_ids:
        return False, "runner_mismatch"

    now = utcnow()
    requested_status = "cancelled" if cancelling else "failed"
    exit_code = None if cancelling else 1
    runner_name = str(getattr(runner, "name", "") or "remote runner")
    runner_hostname = str(getattr(runner, "hostname", "") or runner_name)

    execution_slice.status = requested_status
    execution_slice.assigned_runner_id = (
        execution_slice.assigned_runner_id or runner.id
    )
    execution_slice.runner_name = execution_slice.runner_name or runner_name
    execution_slice.runner_hostname = (
        execution_slice.runner_hostname or runner_hostname
    )
    execution_slice.finished_at = now
    execution_slice.exit_code = exit_code
    execution_slice.dispatch_token = ""
    execution_slice.message = (
        "Remote runner {} went offline; this execution slice was {} and "
        "was not automatically retried."
    ).format(
        runner_name,
        "cancelled" if cancelling else "failed",
    )

    step = execution_slice.step
    existing_by_host = {item.host: item for item in step.host_results}
    for host in execution_slice.get_hosts():
        row = existing_by_host.get(host)
        if row is None:
            row = JobStepHostResult(
                step=step,
                host=host,
                status=requested_status,
            )
            step.host_results.append(row)
            existing_by_host[host] = row
        row.status = requested_status
        row.exit_code = exit_code
        row.stderr = execution_slice.message
        row.runner_id = execution_slice.assigned_runner_id
        row.runner_name = execution_slice.runner_name
        row.runner_hostname = execution_slice.runner_hostname
        row.runner_local = False

    _aggregate_step(step)
    job = step.job
    _reconcile_dependencies(job)
    _finalize_job_if_complete(job)
    db.session.commit()
    return True, requested_status
