"""Atomic remote-runner Job assignment and routing checks."""

import json
import secrets
from datetime import datetime, timezone

from app import db
from app.models import Job
from app.services.runners import runner_health
from app.services.project_concurrency import job_can_start
from app.services.runner_environments import (
    job_step_environment_requirement,
    runner_environment_local_path,
    runner_environment_ready,
)


def utcnow():
    return datetime.now(timezone.utc)


def _required_capabilities(job):
    try:
        value = json.loads(job.required_runner_capabilities_json or "[]")
    except (TypeError, ValueError):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _runner_has_required_environments(runner, job):
    if job.execution_type == "shell":
        return True
    return all(
        runner_environment_ready(
            runner,
            job_step_environment_requirement(step),
        )
        for step in job.steps
    )


def _step_environment_path(runner, job, step):
    if job.execution_type == "shell":
        return step.environment_path
    requirement = job_step_environment_requirement(step)
    if requirement is None:
        return step.environment_path
    return runner_environment_local_path(runner, requirement)


def runner_can_claim(runner, job):
    if runner_health(runner) != "healthy":
        return False
    if runner.running_steps >= runner.max_concurrent_steps:
        return False
    if job.dispatch_target != "remote" or job.status != "queued":
        return False
    if not job_can_start(job):
        return False
    if job.required_runner_id is not None and runner.id != job.required_runner_id:
        return False
    required_site = str(job.required_runner_site or "").strip().lower()
    if required_site and str(runner.site or "").strip().lower() != required_site:
        return False
    if not _required_capabilities(job).issubset(runner.capabilities()):
        return False
    return _runner_has_required_environments(runner, job)


def claim_next_remote_job(runner):
    """Claim one eligible queued remote Job using a conditional UPDATE."""
    candidates = (
        Job.query
        .filter(Job.status == "queued", Job.dispatch_target == "remote")
        .order_by(Job.queued_at.asc(), Job.id.asc())
        .all()
    )
    for candidate in candidates:
        if not runner_can_claim(runner, candidate):
            continue
        token = secrets.token_urlsafe(32)
        assigned_at = utcnow()
        updated = (
            Job.query
            .filter(
                Job.id == candidate.id,
                Job.status == "queued",
                Job.dispatch_target == "remote",
                Job.assigned_runner_id.is_(None),
            )
            .update(
                {
                    Job.assigned_runner_id: runner.id,
                    Job.assigned_at: assigned_at,
                    Job.dispatch_token: token,
                    Job.message: "Assigned to remote runner {}.".format(runner.name),
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.session.rollback()
            continue
        db.session.commit()
        return db.session.get(Job, candidate.id), token
    return None, None


def job_assignment_manifest(
    job, token, repository_artifacts=None, execution_data_url=None
):
    repository_artifacts = repository_artifacts or []
    return {
        "job_id": job.id,
        "project_name": job.project_name,
        "execution_type": job.execution_type,
        "dispatch_token": token,
        "assigned_at": job.assigned_at.isoformat() if job.assigned_at else None,
        "repositories": repository_artifacts,
        "execution_data": {
            "url": execution_data_url,
            "encrypted": True,
            "envelope_version": 1,
        } if execution_data_url else None,
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
                "environment_path": _step_environment_path(
                    job.assigned_runner, job, step
                ),
                "ansible_config_path": step.ansible_config_path,
                "limit": step.limit,
                "tags": step.tags,
                "skip_tags": step.skip_tags,
                "verbosity": step.verbosity,
                "check_mode": bool(step.check_mode),
                "continue_on_failure": step.continue_on_failure,
                "failure_only": step.failure_only,
                "remote_shell_become": step.remote_shell_become,
                "remote_shell_serial": step.remote_shell_serial,
                "refresh_inventory_after": step.refresh_inventory_after,
                "depends_on": step.get_dependency_positions(),
            }
            for step in job.steps
        ],
    }
