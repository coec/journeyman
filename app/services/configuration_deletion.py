"""Safe destructive removal of Projects/Packages and their Job history."""

import os
import shutil
from pathlib import Path

from flask import current_app

from app import db
from app.models import Job, JobPackageSnapshot, NotificationRule
from app.services.job_inventory_snapshot import cleanup_job_inventory_snapshot_files
from app.services.job_cancellation import recover_stale_cancelling_jobs
from app.services.runner_artifacts import cleanup_job_repository_artifacts


class ConfigurationDeletionError(RuntimeError):
    """A Project or Package cannot be safely deleted."""


DELETABLE_JOB_STATUSES = {"successful", "failed", "cancelled"}


def _nonterminal_jobs(jobs):
    return [
        job
        for job in jobs
        if str(job.status or "").strip().lower() not in DELETABLE_JOB_STATUSES
    ]


def _active_job_message(label, jobs):
    identifiers = ", ".join("#{}".format(job.id) for job in jobs[:10])
    if len(jobs) > 10:
        identifiers += ", …"
    return (
        '{} cannot be deleted while associated Jobs are active ({}). '
        'Wait for them to finish or cancel them first.'
    ).format(label, identifiers)


def jobs_for_package(package):
    if package is None or package.id is None:
        return []
    return (
        Job.query
        .join(JobPackageSnapshot, JobPackageSnapshot.job_id == Job.id)
        .filter(JobPackageSnapshot.package_id == package.id)
        .order_by(Job.id.asc())
        .all()
    )


def _delete_notification_rules(scope_type, scope_ids):
    identifiers = [int(value) for value in scope_ids if value is not None]
    if not identifiers:
        return
    (
        NotificationRule.query
        .filter(
            NotificationRule.scope_type == scope_type,
            NotificationRule.scope_id.in_(identifiers),
        )
        .delete(synchronize_session=False)
    )


def _safe_local_job_directory(job_id):
    root = Path(
        os.environ.get("JOURNEYMAN_JOB_ROOT", "/var/lib/journeyman/jobs")
    ).resolve()
    directory = (root / str(int(job_id))).resolve()
    if root not in directory.parents:
        raise ConfigurationDeletionError("Unsafe local Job workspace path.")
    return directory


def cleanup_job_output(job_ids):
    """Best-effort cleanup of filesystem state belonging only to deleted Jobs."""
    errors = []
    for job_id in sorted({int(value) for value in job_ids}):
        try:
            cleanup_job_inventory_snapshot_files(job_id)
        except Exception as exc:  # database deletion has already committed
            current_app.logger.exception(
                "Unable to remove inventory snapshots for deleted Job #%s", job_id
            )
            errors.append("Job #{} inventory snapshots: {}".format(job_id, exc))

        try:
            cleanup_job_repository_artifacts(job_id)
        except Exception as exc:
            current_app.logger.exception(
                "Unable to remove repository artifacts for deleted Job #%s", job_id
            )
            errors.append("Job #{} repository artifacts: {}".format(job_id, exc))

        try:
            workspace = _safe_local_job_directory(job_id)
            if workspace.exists():
                shutil.rmtree(workspace)
        except Exception as exc:
            current_app.logger.exception(
                "Unable to remove local workspace for deleted Job #%s", job_id
            )
            errors.append("Job #{} local workspace: {}".format(job_id, exc))

    return errors


def delete_project_with_job_history(project):
    """Delete a Project and terminal Job history after dependency checks."""
    if project.packages:
        raise ConfigurationDeletionError(
            "This Project is used by one or more Packages. Delete or reassign "
            "those Packages first."
        )

    jobs = list(project.jobs)
    recover_stale_cancelling_jobs(jobs=jobs)
    active = _nonterminal_jobs(jobs)
    if active:
        raise ConfigurationDeletionError(_active_job_message("This Project", active))

    job_ids = [job.id for job in jobs]
    step_ids = [step.id for step in project.steps]
    project_id = project.id

    _delete_notification_rules("project", [project_id])
    _delete_notification_rules("project_step", step_ids)
    for job in jobs:
        db.session.delete(job)
    db.session.delete(project)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ConfigurationDeletionError("Unable to delete the Project.") from exc

    return job_ids, cleanup_job_output(job_ids)


def delete_package_with_job_history(package):
    """Delete a Package and Jobs launched through that Package."""
    if package.reactors:
        raise ConfigurationDeletionError(
            "This Package is used by one or more Reactors. Delete or reassign "
            "those Reactors first."
        )

    jobs = jobs_for_package(package)
    recover_stale_cancelling_jobs(jobs=jobs)
    active = _nonterminal_jobs(jobs)
    if active:
        raise ConfigurationDeletionError(_active_job_message("This Package", active))

    job_ids = [job.id for job in jobs]
    package_id = package.id

    _delete_notification_rules("package", [package_id])
    for job in jobs:
        db.session.delete(job)
    db.session.delete(package)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ConfigurationDeletionError("Unable to delete the Package.") from exc

    return job_ids, cleanup_job_output(job_ids)
