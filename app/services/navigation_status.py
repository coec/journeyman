"""Lightweight status used by the global Journeyman navigation bar."""

from app.models import Job, RunnerEnvironmentSync


def visible_running_jobs_query(username, *, is_admin=False):
    """Return current Jobs visible to one authenticated identity.

    Current activities includes Jobs that are either queued or running.
    """

    query = Job.query.filter(Job.status.in_(("queued", "running")))
    if not is_admin:
        query = query.filter(Job.requested_by == username)
    return query


def visible_running_environment_sync_count(username, *, is_admin=False):
    """Return active Environment synchronizations visible to one identity."""

    query = RunnerEnvironmentSync.query.filter(
        RunnerEnvironmentSync.status.in_(("queued", "building"))
    )
    if not is_admin:
        query = query.filter(RunnerEnvironmentSync.requested_by == username)
    return query.count()


def visible_running_job_count(username, *, is_admin=False):
    """Return the number of current activities visible to an identity."""

    return (
        visible_running_jobs_query(username, is_admin=is_admin).count()
        + visible_running_environment_sync_count(username, is_admin=is_admin)
    )
