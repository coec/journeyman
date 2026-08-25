"""Declarative Repository configuration shared by UI/API clients."""

from dataclasses import dataclass

from flask import current_app

from app import db
from app.credential_types import CREDENTIAL_TYPE_SOURCE_CONTROL
from app.models import Credential, ProjectStep, Repository
from app.services.builtin_automation import BUILTIN_REPOSITORY_URL
from app.services.git import (
    GitError as RepositoryConfigurationError,
    remove_repository_checkout,
    validate_directory_repository_path,
)
from app.services.outbound_security import OutboundSecurityError, validate_repository_url


@dataclass(frozen=True)
class RepositoryConfigurationResult:
    repository: Repository | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def _credential_id(name):
    name = _clean(name)
    if not name:
        return None
    credential = Credential.query.filter_by(name=name).first()
    if credential is None or credential.credential_type != CREDENTIAL_TYPE_SOURCE_CONTROL:
        raise RepositoryConfigurationError(
            'Source Control credential "{}" is missing or invalid.'.format(name)
        )
    return credential.id


def _normalise(repository, values):
    repository_type = _clean(values.get("repository_type")) or "git"
    if repository_type not in {"git", "directory"}:
        raise RepositoryConfigurationError("Repository type must be Git or Directory.")

    name = _clean(values.get("name"))
    if not name:
        raise RepositoryConfigurationError("Name is required.")

    description = _clean(values.get("description"))
    url = _clean(values.get("url"))
    directory_path = _clean(values.get("directory_path"))
    default_branch = _clean(values.get("default_branch")) or "main"
    credential_id = None

    if repository_type == "git":
        directory_path = ""
        if not url:
            raise RepositoryConfigurationError(
                "Repository URL is required for Git repositories."
            )
        if url != BUILTIN_REPOSITORY_URL:
            try:
                url = validate_repository_url(url)
            except OutboundSecurityError as exc:
                raise RepositoryConfigurationError(str(exc)) from exc
        credential_id = _credential_id(values.get("credential"))
        if url.lower().startswith("https://") and credential_id is None:
            raise RepositoryConfigurationError(
                "HTTPS repositories require a Source Control credential."
            )
    else:
        url = ""
        default_branch = "main"
        repositories = Repository.query.filter_by(repository_type="directory").all()
        try:
            directory_path = validate_directory_repository_path(
                directory_path,
                repositories=repositories,
                exclude_repository_id=repository.id if repository is not None else None,
            )
        except RepositoryConfigurationError:
            raise

    return {
        "name": name,
        "description": description,
        "repository_type": repository_type,
        "url": url,
        "directory_path": directory_path,
        "default_branch": default_branch,
        "credential_id": credential_id,
    }


def configure_repository(values):
    """Create or update a Repository and report Ansible-style idempotency."""

    name = _clean(values.get("name"))
    repository = Repository.query.filter_by(name=name).first() if name else None
    desired = _normalise(repository, values)

    created = repository is None
    if created:
        repository = Repository(name=desired["name"])
        db.session.add(repository)

    previous_source = (
        repository.repository_type or "git",
        repository.url or "",
        repository.directory_path or "",
        repository.default_branch or "main",
        repository.credential_id,
    )
    changed = created or any(
        getattr(repository, key) != value
        for key, value in desired.items()
    )

    for key, value in desired.items():
        setattr(repository, key, value)

    current_source = (
        repository.repository_type or "git",
        repository.url or "",
        repository.directory_path or "",
        repository.default_branch or "main",
        repository.credential_id,
    )
    if not created and current_source != previous_source:
        repository.status = "never_synced"
        repository.last_sync_at = None
        repository.last_sync_message = None
        repository.last_commit = None
        repository.last_commit_message = None
        repository.last_commit_author = None
        repository.last_commit_at = None

    if changed:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise RepositoryConfigurationError("Repository name must be unique.") from exc

    return RepositoryConfigurationResult(
        repository=repository,
        changed=changed,
        message=(
            'Repository "{}" created.'.format(repository.name)
            if created
            else (
                'Repository "{}" updated.'.format(repository.name)
                if changed
                else 'Repository "{}" is already configured.'.format(repository.name)
            )
        ),
    )


def delete_repository(name):
    repository = Repository.query.filter_by(name=_clean(name)).first()
    if repository is None:
        return RepositoryConfigurationResult(
            repository=None,
            changed=False,
            message='Repository "{}" is already absent.'.format(_clean(name)),
        )

    project_step = ProjectStep.query.filter_by(repository_id=repository.id).first()
    if project_step is not None:
        raise RepositoryConfigurationError(
            'Repository "{}" cannot be deleted because it is used by one or more project steps.'.format(
                repository.name
            )
        )

    repository_name = repository.name
    try:
        remove_repository_checkout(repository, current_app.config["REPOSITORY_ROOT"])
        db.session.delete(repository)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise RepositoryConfigurationError(
            'Unable to delete repository "{}".'.format(repository_name)
        ) from exc

    return RepositoryConfigurationResult(
        repository=None,
        changed=True,
        message='Repository "{}" deleted.'.format(repository_name),
    )
