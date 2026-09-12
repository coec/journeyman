"""Immutable Project executable-definition snapshots for v2 approval workflows."""

import hashlib
import json

from sqlalchemy import func

from app import db
from app.models import ProjectRevision


def _reference(item):
    if item is None:
        return None
    return {
        "id": item.id,
        "name": item.name,
    }


def _repository_snapshot(repository):
    if repository is None:
        return None
    return {
        "id": repository.id,
        "name": repository.name,
        "repository_type": repository.repository_type,
        "url": repository.url,
        "directory_path": repository.directory_path,
        "default_branch": repository.default_branch,
        "last_commit": repository.last_commit,
    }


def _inventory_snapshot(inventory):
    if inventory is None:
        return None
    return {
        "id": inventory.id,
        "name": inventory.name,
        "inventory_type": inventory.inventory_type,
        "endpoint": inventory.endpoint,
        "credential_id": inventory.credential_id,
        "verify_tls": bool(inventory.verify_tls),
        "enabled": bool(inventory.enabled),
        "config_json": inventory.config_json or "{}",
    }


def _environment_snapshot(environment):
    if environment is None:
        return None
    return {
        "id": environment.id,
        "name": environment.name,
        "path": environment.path,
        "enabled": bool(environment.enabled),
        "is_builtin": bool(environment.is_builtin),
        "is_managed": bool(environment.is_managed),
        "python_interpreter": environment.python_interpreter,
        "ansible_spec": environment.ansible_spec,
        "ansible_config_path": environment.ansible_config_path,
        "pip_requirements": environment.pip_requirements,
        "system_requirements": environment.system_requirements,
        "collection_requirements": environment.collection_requirements,
    }


def _step_snapshot(step):
    effective_repository = step.effective_repository()
    effective_environment = step.effective_environment()
    effective_credentials = step.effective_credentials()

    return {
        "id": step.id,
        "position": step.position,
        "name": step.name,
        "enabled": bool(step.enabled),
        "execution_type": step.execution_type,
        "playbook": step.playbook,
        "limit": step.limit,
        "tags": step.tags,
        "skip_tags": step.skip_tags,
        "extra_vars": step.get_extra_vars(),
        "verbosity": step.verbosity,
        "check_mode": bool(step.check_mode),
        "remote_shell_become": bool(step.remote_shell_become),
        "remote_shell_serial": step.remote_shell_serial,
        "continue_on_failure": bool(step.continue_on_failure),
        "failure_only": bool(step.failure_only),
        "refresh_repository": bool(step.refresh_repository),
        "refresh_inventory_after": bool(step.refresh_inventory_after),
        "oversight_after": bool(step.oversight_after),
        "credentials_override": bool(step.credentials_override),
        "depends_on": step.get_dependency_positions(),
        "repository": _repository_snapshot(step.repository),
        "effective_repository": _repository_snapshot(effective_repository),
        "inventory": _inventory_snapshot(step.inventory),
        "environment": _environment_snapshot(step.environment),
        "effective_environment": _environment_snapshot(effective_environment),
        "credentials": [
            _reference(credential)
            for credential in sorted(
                effective_credentials,
                key=lambda item: (item.name.lower(), item.id or 0),
            )
        ],
    }


def build_project_revision_snapshot(project):
    """Return the canonical executable definition used for approval hashing."""
    return {
        "schema_version": 1,
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "enabled": bool(project.enabled),
            "execution_type": project.execution_type,
            "max_parallel_steps": project.max_parallel_steps,
            "concurrency_policy": project.concurrency_policy,
            "oversight_required_between_all_steps": bool(
                project.oversight_required_between_all_steps
            ),
            "runner_routing": project.runner_routing,
            "runner_site": project.runner_site,
            "runner": _reference(project.runner),
            "default_runner": _reference(project.default_runner),
            "default_runner_crew": _reference(project.default_runner_crew),
            "inventory": _inventory_snapshot(project.inventory),
            "repository": _repository_snapshot(project.repository),
            "environment": _environment_snapshot(project.environment),
            "credentials": [
                _reference(credential)
                for credential in sorted(
                    project.credentials,
                    key=lambda item: (item.name.lower(), item.id or 0),
                )
            ],
        },
        "steps": [
            _step_snapshot(step)
            for step in sorted(project.steps, key=lambda item: item.position)
        ],
    }


def canonical_project_revision_json(snapshot):
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def project_revision_digest(snapshot):
    payload = canonical_project_revision_json(snapshot).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capture_project_revision(project, *, created_by):
    """Persist a new immutable Project revision and return it."""
    if project.id is None:
        db.session.flush()

    snapshot = build_project_revision_snapshot(project)
    snapshot_json = canonical_project_revision_json(snapshot)
    digest = project_revision_digest(snapshot)

    last_sequence = (
        db.session.query(func.max(ProjectRevision.sequence))
        .filter(ProjectRevision.project_id == project.id)
        .scalar()
    )

    revision = ProjectRevision(
        project=project,
        sequence=(last_sequence or 0) + 1,
        digest=digest,
        snapshot_json=snapshot_json,
        created_by=(created_by or "system").strip() or "system",
    )
    db.session.add(revision)
    db.session.flush()
    return revision
