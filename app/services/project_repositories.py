"""Repository refresh behaviour shared by previews and queued executions."""

from datetime import datetime, timezone

from flask import current_app

from app import db
from app.services.git import GitError, sync_repository


class ProjectRepositoryRefreshError(RuntimeError):
    """A repository selected for pre-run refresh could not be synchronized."""


def refresh_project_repositories(project):
    """Refresh each distinct repository requested by an enabled Project step."""
    repositories = {}
    for step in project.steps:
        repository = step.effective_repository()
        if step.enabled and step.refresh_repository and repository is not None:
            repositories.setdefault(repository.id, repository)

    for repository in repositories.values():
        repository.status = "syncing"
        db.session.commit()
        try:
            commit = sync_repository(
                repository,
                current_app.config["REPOSITORY_ROOT"],
                token=None,
            )
        except GitError as exc:
            repository.status = "failed"
            repository.last_sync_at = datetime.now(timezone.utc)
            repository.last_sync_message = str(exc)
            db.session.commit()
            raise ProjectRepositoryRefreshError(
                f'Repository "{repository.name}" refresh failed: {exc}'
            ) from exc

        repository.status = "up_to_date"
        repository.last_sync_at = datetime.now(timezone.utc)
        repository.last_sync_message = (
            "Directory snapshot refreshed successfully before project execution."
            if getattr(repository, "repository_type", "git") == "directory"
            else "Repository synchronized successfully before project execution."
        )
        repository.last_commit = commit.sha
        repository.last_commit_message = commit.message
        repository.last_commit_author = commit.author
        repository.last_commit_at = commit.committed_at
        db.session.commit()
