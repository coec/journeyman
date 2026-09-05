"""Atomic remote-runner execution-slice assignment and manifests."""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.models import Job, JobStep, JobStepExecutionSlice, Runner
from app.services.runners import runner_health
from app.services.runner_environments import (
    job_step_environment_requirement,
    runner_environment_local_path,
    runner_environment_ready,
    runner_environment_state,
)
from app.services.project_oversight import (
    dependency_state,
    update_job_oversight_state,
)
from app.services.project_concurrency import job_can_start


def utcnow():
    return datetime.now(timezone.utc)


def _has_failure_branch(step, steps):
    return any(
        candidate.failure_only
        and step.position in candidate.get_dependency_positions()
        for candidate in steps
    )


def _dependency_state(step, steps_by_position):
    return dependency_state(step, steps_by_position)


def reconcile_non_runnable_steps(job):
    """Mark dependency-blocked/skipped pending steps so later work can progress."""
    changed = False
    steps_by_position = {step.position: step for step in job.steps}
    while True:
        progress = False
        for step in job.steps:
            if step.status != "pending":
                continue
            state = _dependency_state(step, steps_by_position)
            if state in {"blocked", "skipped"}:
                step.status = state
                step.finished_at = utcnow()
                for execution_slice in step.execution_slices:
                    if execution_slice.status == "pending":
                        execution_slice.status = state
                        execution_slice.finished_at = step.finished_at
                        execution_slice.message = (
                            "Workflow dependency did not succeed."
                            if state == "blocked"
                            else "Workflow failure branch was not selected."
                        )
                progress = True
                changed = True
                continue

            # Failed-host-only reruns preserve the original workflow but can
            # legitimately leave a step with no selected hosts.  Such slices
            # are materialised as already-successful zero-host slices.  Once
            # the workflow dependencies make that step eligible, treat the
            # step as a successful no-op rather than attempting to execute an
            # empty --limit (which Ansible would interpret as no restriction).
            slices = list(step.execution_slices)
            if (
                state == "eligible"
                and slices
                and all(
                    item.host_count == 0 and item.status == "successful"
                    for item in slices
                )
            ):
                step.status = "successful"
                step.exit_code = 0
                step.finished_at = utcnow()
                progress = True
                changed = True
        if not progress:
            break
    if changed:
        db.session.flush()
    return changed


def runner_can_claim_slice(runner, execution_slice):
    step = execution_slice.step
    job = step.job if step is not None else None
    if runner.drain_job_id is not None:
        return False
    if runner_health(runner) != "healthy":
        return False
    if runner.running_steps >= runner.max_concurrent_steps:
        return False
    if execution_slice.dispatch_target != "remote" or execution_slice.status != "pending":
        return False
    if execution_slice.required_runner_id != runner.id:
        return False
    if job is None or job.status not in {"queued", "running"}:
        return False
    if not job_can_start(job):
        return False
    if update_job_oversight_state(job):
        db.session.commit()
        return False
    if step.status not in {"pending", "running"}:
        return False
    steps_by_position = {item.position: item for item in job.steps}
    if _dependency_state(step, steps_by_position) != "eligible":
        return False
    if not execution_slice.get_required_capabilities().issubset(runner.capabilities()):
        return False
    if job.execution_type != "shell" and not runner_environment_ready(
        runner, job_step_environment_requirement(step)
    ):
        return False
    return True


def claim_next_remote_slice(runner):
    """Claim one eligible remote execution slice using a conditional UPDATE."""
    jobs = (
        Job.query
        .filter(Job.status.in_(["queued", "running"]))
        .order_by(Job.queued_at.asc(), Job.id.asc())
        .all()
    )
    for job in jobs:
        reconcile_non_runnable_steps(job)
        update_job_oversight_state(job)
        db.session.commit()

    candidates = (
        JobStepExecutionSlice.query
        .join(JobStep, JobStepExecutionSlice.job_step_id == JobStep.id)
        .join(Job, JobStep.job_id == Job.id)
        .filter(
            Job.status.in_(["queued", "running"]),
            JobStepExecutionSlice.status == "pending",
            JobStepExecutionSlice.dispatch_target == "remote",
            JobStepExecutionSlice.required_runner_id == runner.id,
        )
        .order_by(Job.queued_at.asc(), Job.id.asc(), JobStep.position.asc(), JobStepExecutionSlice.position.asc())
        .all()
    )

    for candidate in candidates:
        step = candidate.step
        job = step.job if step is not None else None
        requirement = (
            job_step_environment_requirement(step)
            if job is not None and job.execution_type != "shell"
            else None
        )
        if requirement is not None and runner_environment_state(
            runner, requirement
        ) == "out_of_date":
            from app.models import RunnerEnvironment
            from app.services.runner_slice_lifecycle import fail_pending_remote_slice

            reported = RunnerEnvironment.query.filter_by(
                Runner_id=runner.id,
                environment_id=requirement.environment_id,
            ).one_or_none()
            message = (
                'Cannot assign step {} to runner "{}": Environment "{}" '
                "requires revision {}, but the runner reports revision {}. "
                "The saved Job snapshot can no longer be satisfied."
            ).format(
                step.position,
                runner.name,
                requirement.name,
                str(requirement.revision or "")[:12],
                str(
                    reported.environment_revision if reported is not None else ""
                )[:12] or "not reported",
            )
            fail_pending_remote_slice(candidate, message)
            continue

        if not runner_can_claim_slice(runner, candidate):
            continue

        # Serialize claim against disruptive runner-management drain
        # acquisition. request_runner_drain() locks the same Runner row.
        locked_runner = (
            db.session.execute(
                select(Runner)
                .where(Runner.id == runner.id)
                .with_for_update()
            )
            .unique()
            .scalar_one_or_none()
        )
        if locked_runner is None or locked_runner.drain_job_id is not None:
            db.session.rollback()
            return None, None
        if not runner_can_claim_slice(locked_runner, candidate):
            db.session.rollback()
            continue

        token = secrets.token_urlsafe(32)
        assigned_at = utcnow()
        updated = (
            JobStepExecutionSlice.query
            .filter(
                JobStepExecutionSlice.id == candidate.id,
                JobStepExecutionSlice.status == "pending",
                JobStepExecutionSlice.assigned_runner_id.is_(None),
            )
            .update(
                {
                    JobStepExecutionSlice.assigned_runner_id: locked_runner.id,
                    JobStepExecutionSlice.assigned_at: assigned_at,
                    JobStepExecutionSlice.dispatch_token: token,
                    JobStepExecutionSlice.status: "assigned",
                    JobStepExecutionSlice.message: (
                        "Assigned to remote runner {}.".format(
                            locked_runner.name
                        )
                    ),
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.session.rollback()
            continue

        job = db.session.get(Job, candidate.step.job_id)
        step = db.session.get(JobStep, candidate.job_step_id)
        if job.status == "queued":
            job.status = "running"
            job.started_at = assigned_at
            job.finished_at = None
            job.exit_code = None
            job.message = "Execution slices are running."
        if step.status == "pending":
            step.status = "running"
            step.started_at = assigned_at
            step.finished_at = None
            step.exit_code = None
        db.session.commit()
        return db.session.get(JobStepExecutionSlice, candidate.id), token
    return None, None


def _slice_environment_path(execution_slice):
    step = execution_slice.step
    job = step.job
    if job.execution_type == "shell":
        return step.environment_path
    requirement = job_step_environment_requirement(step)
    if requirement is None:
        return step.environment_path
    return runner_environment_local_path(execution_slice.assigned_runner, requirement)


def slice_assignment_manifest(execution_slice, token, repository_artifacts=None, execution_data_url=None):
    repository_artifacts = repository_artifacts or []
    step = execution_slice.step
    job = step.job
    hosts = execution_slice.get_hosts()
    return {
        "assignment_type": "slice",
        "job_id": job.id,
        "slice_id": execution_slice.id,
        "project_name": job.project_name,
        "execution_type": job.execution_type,
        "dispatch_token": token,
        "assigned_at": execution_slice.assigned_at.isoformat() if execution_slice.assigned_at else None,
        "repositories": repository_artifacts,
        "execution_data": {
            "url": execution_data_url,
            "encrypted": True,
            "envelope_version": 1,
        } if execution_data_url else None,
        "start_url": "/api/runners/slices/{}/start".format(execution_slice.id),
        "control_url": "/api/runners/slices/{}/control".format(execution_slice.id),
        "output_url": "/api/runners/slices/{}/output".format(execution_slice.id),
        "complete_url": "/api/runners/slices/{}/complete".format(execution_slice.id),
        "steps": [
            {
                "id": step.id,
                "position": step.position,
                "name": step.name,
                "playbook": step.playbook,
                "repository_snapshot_id": step.job_repository_snapshot_id,
                "environment_name": step.environment_name,
                "environment_id": step.environment_id,
                "environment_revision": step.environment_revision,
                "environment_path": _slice_environment_path(execution_slice),
                "ansible_config_path": step.ansible_config_path,
                "limit": ",".join(hosts),
                "tags": step.tags,
                "skip_tags": step.skip_tags,
                "verbosity": step.verbosity,
                "check_mode": bool(step.check_mode),
                "continue_on_failure": False,
                "failure_only": False,
                "remote_shell_become": step.remote_shell_become,
                "remote_shell_serial": step.remote_shell_serial,
                "refresh_inventory_after": False,
                "depends_on": [],
            }
        ],
    }
