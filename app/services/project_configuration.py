"""Declarative Project configuration shared by the REST API and future clients."""

from dataclasses import dataclass
import json

from app import db
from app.models import Credential, Environment, Inventory, Project, ProjectPackage, ProjectStep, Repository
from app.services.name_ordering import reserved_name_validation_error
from app.services.project_concurrency import normalise_concurrency_policy
from app.services.configuration_deletion import (
    ConfigurationDeletionError,
    delete_project_with_job_history,
)


class ProjectConfigurationError(ValueError):
    pass


@dataclass
class ProjectConfigurationResult:
    changed: bool
    project: Project | None
    message: str


def _name(value):
    return str(value or "").strip()


def _lookup(model, name, label, *, required=False):
    value = _name(name)
    if not value:
        if required:
            raise ProjectConfigurationError(f"{label} is required.")
        return None
    row = model.query.filter(model.name == value).one_or_none()
    if row is None:
        raise ProjectConfigurationError(f'{label} "{value}" does not exist.')
    return row


def _lookup_credentials(values):
    if values is None:
        return []
    if not isinstance(values, list):
        raise ProjectConfigurationError("Credentials must be a list of names.")
    rows = []
    seen = set()
    for item in values:
        name = _name(item)
        if not name or name in seen:
            continue
        row = _lookup(Credential, name, "Credential", required=True)
        rows.append(row)
        seen.add(name)
    return rows


def _normalise_steps(values, *, default_repository, default_inventory, default_environment):
    if not isinstance(values, list) or not values:
        raise ProjectConfigurationError("At least one Project step is required.")

    names = []
    for position, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ProjectConfigurationError(f"Step {position} must be an object.")
        step_name = _name(item.get("name")) or f"Step {position}"
        if step_name in names:
            raise ProjectConfigurationError(f'Step name "{step_name}" is duplicated.')
        names.append(step_name)

    positions = {name: position for position, name in enumerate(names, start=1)}
    rows = []
    for position, item in enumerate(values, start=1):
        step_name = names[position - 1]
        repository = _lookup(Repository, item.get("repository"), "Repository") if _name(item.get("repository")) else None
        inventory = _lookup(Inventory, item.get("inventory"), "Inventory") if _name(item.get("inventory")) else None
        environment = _lookup(Environment, item.get("environment"), "Environment") if _name(item.get("environment")) else None
        credentials = _lookup_credentials(item.get("credentials", []))

        playbook = _name(item.get("playbook"))
        extra_vars = item.get("extra_vars", {})
        if extra_vars is None:
            extra_vars = {}
        if not isinstance(extra_vars, dict):
            raise ProjectConfigurationError(f"Step {position} extra_vars must be an object.")
        try:
            json.dumps(extra_vars)
        except (TypeError, ValueError) as exc:
            raise ProjectConfigurationError(f"Step {position} extra_vars are not JSON-safe: {exc}.") from exc

        verbosity = int(item.get("verbosity", 0))
        if verbosity < 0 or verbosity > 5:
            raise ProjectConfigurationError(f"Step {position} verbosity must be from 0 to 5.")

        depends_on = item.get("depends_on", []) or []
        if not isinstance(depends_on, list):
            raise ProjectConfigurationError(f"Step {position} depends_on must be a list of step names.")
        dependency_positions = []
        for dependency_name in depends_on:
            dependency_name = _name(dependency_name)
            if dependency_name not in positions:
                raise ProjectConfigurationError(
                    f'Step {position} depends on unknown step "{dependency_name}".'
                )
            dependency_position = positions[dependency_name]
            if dependency_position == position:
                raise ProjectConfigurationError(f"Step {position} cannot depend on itself.")
            dependency_positions.append(dependency_position)

        rows.append({
            "position": position,
            "name": step_name,
            "repository": repository,
            "inventory": inventory,
            "environment": environment,
            "credentials": credentials,
            "playbook": playbook,
            "limit": _name(item.get("limit")),
            "tags": _name(item.get("tags")),
            "skip_tags": _name(item.get("skip_tags")),
            "extra_vars": extra_vars,
            "verbosity": verbosity,
            "check_mode": bool(item.get("check_mode", False)),
            "continue_on_failure": bool(item.get("continue_on_failure", False)),
            "failure_only": bool(item.get("failure_only", False)),
            "refresh_repository": bool(item.get("refresh_repository", False)),
            "refresh_inventory_after": bool(item.get("refresh_inventory_after", False)),
            "oversight_after": bool(item.get("oversight_after", False)),
            "credentials_override": "credentials" in item,
            "dependency_positions": sorted(set(dependency_positions)),
            "enabled": bool(item.get("enabled", True)),
        })

    # Detect dependency cycles independently of dispatch readiness.
    dependency_map = {row["position"]: row["dependency_positions"] for row in rows}
    visiting = set()
    visited = set()
    def visit(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dep) for dep in dependency_map.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False
    if any(visit(node) for node in dependency_map):
        raise ProjectConfigurationError("Project step dependencies contain a cycle.")

    return rows


def _project_state(project):
    return {
        "name": project.name,
        "description": project.description or "",
        "execution_type": project.execution_type or "ansible",
        "inventory": project.inventory.name if project.inventory else "",
        "repository": project.repository.name if project.repository else "",
        "environment": project.environment.name if project.environment else "",
        "credentials": [row.name for row in project.credentials],
        "max_parallel_steps": project.max_parallel_steps,
        "concurrency_policy": project.concurrency_policy or "unrestricted",
        "oversight_required_between_all_steps": bool(project.oversight_required_between_all_steps),
        "enabled": bool(project.enabled),
        "steps": [
            {
                "name": step.name,
                "repository": step.repository.name if step.repository else "",
                "inventory": step.inventory.name if step.inventory else "",
                "environment": step.environment.name if step.environment else "",
                "credentials": [row.name for row in step.credentials] if step.credentials_override else [],
                "credentials_override": bool(step.credentials_override),
                "playbook": step.playbook or "",
                "limit": step.limit or "",
                "tags": step.tags or "",
                "skip_tags": step.skip_tags or "",
                "extra_vars": step.get_extra_vars(),
                "verbosity": step.verbosity,
                "check_mode": bool(step.check_mode),
                "continue_on_failure": bool(step.continue_on_failure),
                "failure_only": bool(step.failure_only),
                "refresh_repository": bool(step.refresh_repository),
                "refresh_inventory_after": bool(step.refresh_inventory_after),
                "oversight_after": bool(step.oversight_after),
                "depends_on": step.get_dependency_positions(),
                "enabled": bool(step.enabled),
            }
            for step in project.steps
        ],
    }


def configure_project(values, *, owner="system"):
    if not isinstance(values, dict):
        raise ProjectConfigurationError("Project configuration must be an object.")
    name = _name(values.get("name"))
    if not name:
        raise ProjectConfigurationError("Project name is required.")
    reserved_error = reserved_name_validation_error(name)
    if reserved_error:
        raise ProjectConfigurationError(reserved_error)

    execution_type = _name(values.get("execution_type")) or "ansible"
    if execution_type not in {"ansible", "shell", "remote_shell"}:
        raise ProjectConfigurationError("Project execution_type is invalid.")
    max_parallel_steps = int(values.get("max_parallel_steps", 4))
    if max_parallel_steps < 1 or max_parallel_steps > 32:
        raise ProjectConfigurationError("Maximum parallel steps must be between 1 and 32.")

    inventory = _lookup(Inventory, values.get("inventory"), "Inventory") if _name(values.get("inventory")) else None
    repository = _lookup(Repository, values.get("repository"), "Repository") if _name(values.get("repository")) else None
    environment = _lookup(Environment, values.get("environment"), "Environment") if _name(values.get("environment")) else None
    credentials = _lookup_credentials(values.get("credentials", []))
    steps = _normalise_steps(
        values.get("steps"),
        default_repository=repository,
        default_inventory=inventory,
        default_environment=environment,
    )

    legacy_concurrency = values.get("allow_concurrent_instances", None)
    default_concurrency = (
        "exclusive" if legacy_concurrency is False else "unrestricted"
    )
    try:
        concurrency_policy = normalise_concurrency_policy(
            values.get("concurrency_policy", default_concurrency)
        )
    except ValueError as exc:
        raise ProjectConfigurationError(str(exc)) from exc

    desired = {
        "name": name,
        "description": _name(values.get("description")),
        "execution_type": execution_type,
        "inventory": inventory.name if inventory else "",
        "repository": repository.name if repository else "",
        "environment": environment.name if environment else "",
        "credentials": [row.name for row in credentials],
        "max_parallel_steps": max_parallel_steps,
        "concurrency_policy": concurrency_policy,
        "oversight_required_between_all_steps": bool(values.get("oversight_required_between_all_steps", False)),
        "enabled": bool(values.get("enabled", True)),
        "steps": [
            {
                "name": row["name"],
                "repository": row["repository"].name if row["repository"] else "",
                "inventory": row["inventory"].name if row["inventory"] else "",
                "environment": row["environment"].name if row["environment"] else "",
                "credentials": [item.name for item in row["credentials"]],
                "credentials_override": bool(row["credentials_override"]),
                "playbook": row["playbook"],
                "limit": row["limit"],
                "tags": row["tags"],
                "skip_tags": row["skip_tags"],
                "extra_vars": row["extra_vars"],
                "verbosity": row["verbosity"],
                "check_mode": row["check_mode"],
                "continue_on_failure": row["continue_on_failure"],
                "failure_only": row["failure_only"],
                "refresh_repository": row["refresh_repository"],
                "refresh_inventory_after": row["refresh_inventory_after"],
                "oversight_after": row["oversight_after"],
                "depends_on": row["dependency_positions"],
                "enabled": row["enabled"],
            }
            for row in steps
        ],
    }

    project = Project.query.filter(Project.name == name).one_or_none()
    if project is not None and project.builtin_key:
        raise ProjectConfigurationError("Built-in Projects cannot be modified through this API.")
    if project is not None and _project_state(project) == desired:
        return ProjectConfigurationResult(False, project, f'Project "{name}" is already configured.')

    if project is None:
        project = Project(name=name, owner=owner)
        db.session.add(project)

    project.description = desired["description"]
    project.execution_type = execution_type
    project.inventory = inventory
    project.repository = repository
    project.environment = environment
    project.credentials = credentials
    project.max_parallel_steps = max_parallel_steps
    project.concurrency_policy = desired["concurrency_policy"]
    project.oversight_required_between_all_steps = desired["oversight_required_between_all_steps"]
    project.enabled = desired["enabled"]

    project.steps.clear()
    # ProjectStep has a unique constraint on (project_id, position).
    # Flush delete-orphans before inserting replacement steps so an update
    # cannot collide with the positions held by the previous definition.
    db.session.flush()

    for row in steps:
        step = ProjectStep(
            position=row["position"],
            name=row["name"],
            repository=row["repository"],
            inventory=row["inventory"],
            environment=row["environment"],
            credentials=row["credentials"],
            playbook=row["playbook"],
            limit=row["limit"],
            tags=row["tags"],
            skip_tags=row["skip_tags"],
            verbosity=row["verbosity"],
            check_mode=row["check_mode"],
            continue_on_failure=row["continue_on_failure"],
            failure_only=row["failure_only"],
            refresh_repository=row["refresh_repository"],
            refresh_inventory_after=row["refresh_inventory_after"],
            oversight_after=row["oversight_after"],
            credentials_override=row["credentials_override"],
            enabled=row["enabled"],
        )
        step.set_extra_vars(row["extra_vars"])
        step.set_dependency_positions(row["dependency_positions"])
        project.steps.append(step)

    db.session.commit()
    action = "created" if project.created_at == project.updated_at else "updated"
    return ProjectConfigurationResult(True, project, f'Project "{name}" {action}.')


def delete_project(name):
    name = _name(name)
    project = Project.query.filter(Project.name == name).one_or_none()
    if project is None:
        return ProjectConfigurationResult(False, None, f'Project "{name}" is already absent.')
    if project.builtin_key:
        raise ProjectConfigurationError("Built-in Projects cannot be deleted through this API.")
    try:
        job_ids, cleanup_errors = delete_project_with_job_history(project)
    except ConfigurationDeletionError as exc:
        raise ProjectConfigurationError(str(exc)) from exc
    message = f'Project "{name}" deleted with {len(job_ids)} associated Job(s).'
    if cleanup_errors:
        message += " Some Job filesystem output could not be removed; check the Journeyman logs."
    return ProjectConfigurationResult(True, None, message)
