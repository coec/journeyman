"""Portable Journeyman configuration export/import.

This module deliberately uses explicit allowlisted serializers. It is not a
generic SQLAlchemy dump facility. Secret values, encrypted blobs, runner
secrets, job state, caches, sessions, and audit history are never exported.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app
from datetime import datetime, timezone

from app import db
from app.services.git import GitError, safe_repository_dir, validate_directory_repository_path

from app.models import (
    Credential,
    Environment,
    Inventory,
    Project,
    ProjectPackage,
    ProjectPackageInput,
    ProjectPackagePermission,
    ProjectSchedule,
    ProjectStep,
    Repository,
    Runner,
    RunnerCrew,
)


FORMAT_VERSION = 1


class ConfigPortabilityError(RuntimeError):
    pass


def _utc_iso():
    return datetime.now(timezone.utc).isoformat()


def _credential_identity(credential):
    if credential is None:
        return None
    return (
        str(credential.name or ""),
        str(credential.credential_type or ""),
    )


def _credential_requirement_records(credentials, internal=False):
    """Return anonymous JXF credential requirements and an object->ref map.

    Credential owners/usernames are never exported. Credential names are
    installation-specific metadata and are exported only for --internal JXF.
    """

    identities = sorted(
        {
            _credential_identity(credential)
            for credential in credentials
            if credential is not None
        }
    )
    refs_by_identity = {
        identity: "credential_{}".format(index)
        for index, identity in enumerate(
            identities,
            start=1,
        )
    }
    requirements = []
    for identity in identities:
        requirement = {
            "ref": refs_by_identity[identity],
            "type": identity[1],
        }
        if internal:
            requirement["name"] = identity[0]
        requirements.append(requirement)
    return requirements, refs_by_identity


def _credential_ref(credential, refs_by_identity):
    identity = _credential_identity(credential)
    if identity is None:
        return None
    return refs_by_identity[identity]



def _load_inventory_config(inventory):
    try:
        value = json.loads(inventory.config_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _inventory_source_names(config):
    names = []
    source_id = config.get("source_inventory_id")
    if source_id:
        source = db.session.get(Inventory, int(source_id))
        if source is not None:
            names.append(source.name)
    for source_id in config.get("source_inventory_ids", []) or []:
        try:
            source = db.session.get(Inventory, int(source_id))
        except (TypeError, ValueError):
            source = None
        if source is not None:
            names.append(source.name)
    return names


def _safe_inventory_config(inventory):
    config = _load_inventory_config(inventory)
    inventory_type = inventory.inventory_type
    result = {}

    if inventory_type == "satellite":
        result = {"organization": str(config.get("organization") or "")}
    elif inventory_type == "zabbix":
        result = {
            "tag_name": str(config.get("tag_name") or ""),
            "tag_value": str(config.get("tag_value") or ""),
            "include_disabled": bool(config.get("include_disabled", False)),
        }
    elif inventory_type == "netbox":
        result = {
            "status": str(config.get("status") or "active"),
            "tag": str(config.get("tag") or ""),
            "site": str(config.get("site") or ""),
            "role": str(config.get("role") or ""),
        }
    elif inventory_type == "lightspeed":
        result = {"tags": str(config.get("tags") or "")}
    elif inventory_type == "ovirt":
        result = {
            "query_filter": config.get("query_filter"),
            "hostname_preference": config.get("hostname_preference") or ["fqdn", "name"],
        }
    elif inventory_type == "filtered":
        source_names = _inventory_source_names(config)
        result = {
            "source_inventory": source_names[0] if source_names else None,
            "include_groups": config.get("include_groups", []) or [],
            "exclude_groups": config.get("exclude_groups", []) or [],
        }
    elif inventory_type == "composite":
        result = {
            "source_inventories": _inventory_source_names(config),
            "normalize_hostnames": str(config.get("normalize_hostnames") or "none"),
        }
    elif inventory_type == "static":
        # Static inventory text is arbitrary user content and may contain
        # passwords/private keys. Never place it in a portable export.
        result = {"content_exported": False}

    append_domain = str(config.get("append_domain") or "")
    if append_domain:
        result["append_domain"] = append_domain
    return result


def _assert_export_safe_network_value(value, label):
    """Reject legacy network URLs containing embedded passwords.

    Current Journeyman forms already reject these, but export is a separate
    security boundary and must not trust historical database contents.
    """

    value = str(value or "")
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ssh://")):
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ConfigPortabilityError(
                "{} contains an invalid URL.".format(label)
            ) from exc
        if parsed.password:
            raise ConfigPortabilityError(
                "{} contains an embedded password and cannot be exported."
                .format(label)
            )
        if lowered.startswith(("http://", "https://")) and parsed.username:
            raise ConfigPortabilityError(
                "{} contains embedded HTTP credentials and cannot be exported."
                .format(label)
            )
    return value


def _repository_data(repository, credential_refs):
    return {
        "name": repository.name,
        "description": repository.description,
        "repository_type": getattr(repository, "repository_type", "git") or "git",
        "url": _assert_export_safe_network_value(
            repository.url,
            "Repository {!r} URL".format(repository.name),
        ),
        "directory_path": getattr(repository, "directory_path", "") or "",
        "default_branch": repository.default_branch,
        "credential": _credential_ref(
            db.session.get(Credential, repository.credential_id)
            if repository.credential_id else None,
            credential_refs,
        ),
    }


def _environment_data(environment):
    return {
        "name": environment.name,
        "path": environment.path,
        "enabled": bool(environment.enabled),
        "is_default": bool(environment.is_default),
        "is_builtin": bool(environment.is_builtin),
        "is_managed": bool(environment.is_managed),
        "python_interpreter": environment.python_interpreter,
        "ansible_spec": environment.ansible_spec,
        "ansible_config_path": environment.ansible_config_path,
        "pip_requirements": environment.pip_requirements,
        "system_requirements": environment.system_requirements,
        "collection_requirements": environment.collection_requirements,
    }


def _inventory_data(inventory, credential_refs):
    return {
        "name": inventory.name,
        "inventory_type": inventory.inventory_type,
        "endpoint": _assert_export_safe_network_value(
            inventory.endpoint,
            "Inventory {!r} endpoint".format(inventory.name),
        ),
        "credential": _credential_ref(
            inventory.credential,
            credential_refs,
        ),
        "verify_tls": bool(inventory.verify_tls),
        "enabled": bool(inventory.enabled),
        "config": _safe_inventory_config(inventory),
    }


def _runner_crew_data(crew):
    return {
        "name": crew.name,
        "description": crew.description,
        "enabled": bool(crew.enabled),
        "runners": [runner.name for runner in crew.runners],
    }


def _step_data(step, credential_refs, internal=False):
    return {
        "position": int(step.position),
        "name": step.name,
        "repository": (
            step.repository.name
            if internal and step.repository
            else None
        ),
        "inventory": step.inventory.name if step.inventory else None,
        "environment": step.environment.name if step.environment else None,
        "playbook": step.playbook,
        "limit": step.limit,
        "tags": step.tags,
        "skip_tags": step.skip_tags,
        "extra_vars": step.get_extra_vars(),
        "verbosity": int(step.verbosity),
        "check_mode": bool(step.check_mode),
        "remote_shell_become": bool(step.remote_shell_become),
        "remote_shell_serial": int(step.remote_shell_serial),
        "enabled": bool(step.enabled),
        "continue_on_failure": bool(step.continue_on_failure),
        "failure_only": bool(step.failure_only),
        "refresh_repository": bool(step.refresh_repository),
        "refresh_inventory_after": bool(step.refresh_inventory_after),
        "oversight_after": bool(step.oversight_after),
        "credentials_override": bool(step.credentials_override),
        "credentials": [
            _credential_ref(
                credential,
                credential_refs,
            )
            for credential in step.credentials
        ],
        "depends_on": step.get_dependency_positions(),
    }


def _schedule_data(schedule):
    return {
        "name": schedule.name,
        "schedule_type": schedule.schedule_type,
        "timezone_name": schedule.timezone_name,
        "start_at": schedule.start_at.isoformat() if schedule.start_at else None,
        "end_at": schedule.end_at.isoformat() if schedule.end_at else None,
        "interval_minutes": schedule.interval_minutes,
        "weekdays": schedule.weekdays,
    }


def _project_data(
    project,
    credential_refs,
    enabled_only=False,
    include_schedules=True,
    internal=False,
):
    return {
        "name": project.name,
        "description": project.description,
        "execution_type": project.execution_type,
        "max_parallel_steps": int(project.max_parallel_steps),
        "concurrency_policy": project.concurrency_policy or "unrestricted",
        "oversight_required_between_all_steps": (
            bool([
                step
                for step in project.steps
                if any(
                    step.position in child.get_dependency_positions()
                    for child in project.steps
                )
            ])
            and all(
                getattr(step, "oversight_after", False)
                for step in project.steps
                if any(
                    step.position in child.get_dependency_positions()
                    for child in project.steps
                )
            )
        ),
        "runner_routing": project.runner_routing,
        "runner_site": project.runner_site,
        "runner": project.runner.name if project.runner else None,
        "default_runner": (
            project.default_runner.name if project.default_runner else None
        ),
        "default_runner_crew": (
            project.default_runner_crew.name
            if project.default_runner_crew else None
        ),
        "repository": (
            project.repository.name
            if internal and project.repository
            else None
        ),
        "inventory": project.inventory.name if project.inventory else None,
        "environment": project.environment.name if project.environment else None,
        "credentials": [
            _credential_ref(
                credential,
                credential_refs,
            )
            for credential in project.credentials
        ],
        # Keep all steps of an enabled Project. Disabled steps are part of the
        # Project definition and dependency graph.
        "steps": [
            _step_data(
                step,
                credential_refs,
                internal=internal,
            )
            for step in project.steps
        ],
        "schedules": (
            [
                _schedule_data(schedule)
                for schedule in project.schedules
                if not enabled_only or schedule.enabled
            ]
            if include_schedules
            else []
        ),
    }


def _package_input_data(item):
    return {
        "position": int(item.position),
        "variable_name": item.variable_name,
        "label": item.label,
        "help_text": item.help_text,
        "input_type": item.input_type,
        "required": bool(item.required),
        "is_secret": bool(item.is_secret),
        # Never export a default from a secret input even if legacy data has
        # one stored.
        "default_value": None if item.is_secret else item.get_default_value(),
        "choices": item.get_choices(),
        "validation": item.get_validation(),
        "conditions": item.get_conditions(),
        "display_role": item.display_role,
        "binding_type": item.binding_type,
        "bind_to_inventory": bool(item.bind_to_inventory),
        "inventory_binding_name": item.inventory_binding_name,
    }


def _package_data(package):
    return {
        "name": package.name,
        "description": package.description,
        "project": package.project.name,
        "warning_message": package.warning_message,
        "confirmation_required": bool(package.confirmation_required),
        "confirmation_message": package.confirmation_message,
        "fixed_vars": package.get_fixed_vars(),
        "inputs": [_package_input_data(item) for item in package.inputs],
    }


def _collect_dependency_names(projects, packages):
    repository_names = set()
    inventory_names = set()
    environment_names = set()
    crew_names = set()

    for project in projects:
        if project.repository:
            repository_names.add(project.repository.name)
        if project.inventory:
            inventory_names.add(project.inventory.name)
        if project.environment:
            environment_names.add(project.environment.name)
        if project.default_runner_crew:
            crew_names.add(project.default_runner_crew.name)
        for step in project.steps:
            if step.repository:
                repository_names.add(step.repository.name)
            if step.inventory:
                inventory_names.add(step.inventory.name)
            if step.environment:
                environment_names.add(step.environment.name)

    # Inventory dependencies are transitive.
    pending = list(inventory_names)
    while pending:
        name = pending.pop()
        inventory = Inventory.query.filter_by(name=name).first()
        if inventory is None:
            continue
        for source_name in _inventory_source_names(_load_inventory_config(inventory)):
            if source_name not in inventory_names:
                inventory_names.add(source_name)
                pending.append(source_name)

    # Packages reference Projects; their project dependencies were already
    # collected by the project pass.
    return repository_names, inventory_names, environment_names, crew_names


def _collect_credentials(
    repositories,
    inventories,
    projects,
    include_repository_credentials=True,
):
    credentials = []

    if include_repository_credentials:
        for repository in repositories:
            if repository.credential_id:
                credential = db.session.get(
                    Credential,
                    repository.credential_id,
                )
                if credential is not None:
                    credentials.append(credential)

    for inventory in inventories:
        if inventory.credential is not None:
            credentials.append(
                inventory.credential
            )

    for project in projects:
        credentials.extend(
            project.credentials
        )
        for step in project.steps:
            credentials.extend(
                step.credentials
            )

    return credentials


def _query_named(model, names):
    names = set(names or ())
    if not names:
        return []
    return (
        model.query
        .filter(model.name.in_(names))
        .order_by(model.name)
        .all()
    )


def _selected_packages(package_names):
    selected = []
    missing = []

    for name in package_names or ():
        value = str(name or "").strip()
        if not value:
            continue
        package = ProjectPackage.query.filter_by(
            name=value
        ).first()
        if package is None:
            missing.append(value)
        elif package not in selected:
            selected.append(package)

    if missing:
        raise ConfigPortabilityError(
            "Package(s) not found: {}."
            .format(
                ", ".join(
                    repr(name)
                    for name in missing
                )
            )
        )

    return sorted(
        selected,
        key=lambda item: item.name.lower(),
    )


def export_configuration(
    enabled_only=False,
    package_names=None,
    internal=False,
):
    package_names = tuple(package_names or ())

    if enabled_only and package_names:
        raise ConfigPortabilityError(
            "--enabled-only and --package cannot be used together."
        )

    projects_query = Project.query.order_by(Project.name)
    packages_query = ProjectPackage.query.order_by(ProjectPackage.name)
    include_schedules = True

    if package_names:
        packages = _selected_packages(package_names)
        project_ids = {
            package.project_id
            for package in packages
        }
        projects = (
            Project.query
            .filter(Project.id.in_(project_ids))
            .order_by(Project.name)
            .all()
        )

        repo_names, inv_names, env_names, crew_names = (
            _collect_dependency_names(
                projects,
                packages,
            )
        )
        repositories = _query_named(
            Repository,
            repo_names,
        )
        inventories = _query_named(
            Inventory,
            inv_names,
        )
        environments = _query_named(
            Environment,
            env_names,
        )
        crews = _query_named(
            RunnerCrew,
            crew_names,
        )

        # A Package exchange must never cause a schedule to appear merely
        # because the selected Package's Project happened to have one.
        include_schedules = False

    elif enabled_only:
        projects = (
            projects_query
            .filter(Project.enabled.is_(True))
            .all()
        )
        project_ids = {
            project.id
            for project in projects
        }
        packages = (
            packages_query
            .filter(ProjectPackage.enabled.is_(True))
            .filter(
                ProjectPackage.project_id.in_(
                    project_ids or {-1}
                )
            )
            .all()
        )
        repo_names, inv_names, env_names, crew_names = (
            _collect_dependency_names(
                projects,
                packages,
            )
        )
        repositories = _query_named(
            Repository,
            repo_names,
        )
        inventories = _query_named(
            Inventory,
            inv_names,
        )
        environments = _query_named(
            Environment,
            env_names,
        )
        crews = _query_named(
            RunnerCrew,
            crew_names,
        )

    else:
        projects = projects_query.all()
        packages = packages_query.all()
        repositories = (
            Repository.query
            .order_by(Repository.name)
            .all()
        )
        inventories = (
            Inventory.query
            .order_by(Inventory.name)
            .all()
        )
        environments = (
            Environment.query
            .order_by(Environment.name)
            .all()
        )
        crews = (
            RunnerCrew.query
            .order_by(RunnerCrew.name)
            .all()
        )

    credential_requirements, credential_refs = (
        _credential_requirement_records(
            _collect_credentials(
                repositories,
                inventories,
                projects,
                include_repository_credentials=internal,
            ),
            internal=internal,
        )
    )

    document = {
        "journeyman_export": {
            "format_version": FORMAT_VERSION,
            "journeyman_version": None,
            "exported_at": _utc_iso(),
            "enabled_only": bool(enabled_only),
            "selected_packages": [
                package.name
                for package in packages
            ] if package_names else [],
            "package_exchange": bool(package_names),
            "contains_secret_material": False,
            "internal": bool(internal),
        },
        "credentials_required": credential_requirements,
        "repositories": (
            [
                _repository_data(
                    item,
                    credential_refs,
                )
                for item in repositories
            ]
            if internal
            else []
        ),
        "environments": [
            _environment_data(item)
            for item in environments
        ],
        "inventories": [
            _inventory_data(
                item,
                credential_refs,
            )
            for item in inventories
        ],
        "runner_crews": [
            _runner_crew_data(item)
            for item in crews
        ],
        "projects": [
            _project_data(
                item,
                credential_refs,
                enabled_only=enabled_only,
                include_schedules=include_schedules,
                internal=internal,
            )
            for item in projects
        ],
        "packages": [
            _package_data(item)
            for item in packages
        ],
    }

    return document


def _portable_slug(value):
    value = str(value or "").strip().lower()
    result = []
    last_dash = False
    for char in value:
        if char.isalnum():
            result.append(char)
            last_dash = False
        elif not last_dash:
            result.append("-")
            last_dash = True
    return "".join(result).strip("-") or "automation"


def collect_export_payload(document):
    """Attach a payload manifest and return archive member bytes.

    Only the primary executable file selected by each Project step is
    packaged. Repository identities are used solely to locate the checked-out
    source file and are not written into a portable JXF.
    """

    project_rows = {
        project.name: project
        for project in Project.query.filter(
            Project.name.in_(
                [
                    str(item.get("name") or "")
                    for item in document.get("projects", []) or []
                ]
                or [""]
            )
        ).all()
    }
    archive_files = {}
    assets = []
    asset_refs = {}
    internal = bool(
        (document.get("journeyman_export") or {}).get("internal", False)
    )

    for project_data in document.get("projects", []) or []:
        project = project_rows.get(str(project_data.get("name") or ""))
        if project is None:
            raise ConfigPortabilityError(
                "Unable to locate exported Project {!r} while collecting payload."
                .format(project_data.get("name"))
            )
        steps_by_position = {int(step.position): step for step in project.steps}
        project_slug = _portable_slug(project.name)

        for step_data in project_data.get("steps", []) or []:
            position = int(step_data.get("position") or 0)
            step = steps_by_position.get(position)
            if step is None:
                raise ConfigPortabilityError(
                    "Project {!r} step {} disappeared while collecting payload."
                    .format(project.name, position)
                )
            repository = step.effective_repository()
            if repository is None:
                raise ConfigPortabilityError(
                    "Project {!r} step {} has no repository; its payload cannot be exported."
                    .format(project.name, position)
                )

            relative = Path(str(step.playbook or ""))
            if (
                not str(relative)
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ConfigPortabilityError(
                    "Project {!r} step {} has an unsafe payload path {!r}."
                    .format(project.name, position, step.playbook)
                )

            checkout = Path(
                safe_repository_dir(
                    current_app.config["REPOSITORY_ROOT"],
                    repository.id,
                )
            )
            source = (checkout / relative).resolve()
            checkout_resolved = checkout.resolve()
            if checkout_resolved not in source.parents or not source.is_file():
                raise ConfigPortabilityError(
                    "Project {!r} step {} payload {!r} is not present in the local repository checkout."
                    .format(project.name, position, step.playbook)
                )

            key = (repository.id, relative.as_posix())
            asset_ref = asset_refs.get(key)
            if asset_ref is None:
                asset_ref = "asset_{}".format(len(assets) + 1)
                asset_refs[key] = asset_ref
                suffix = source.suffix.lower()
                if project.execution_type == "shell":
                    asset_type = "shell_script"
                    suffix = suffix or ".sh"
                    bucket = "scripts"
                else:
                    asset_type = "ansible_playbook"
                    suffix = suffix if suffix in (".yml", ".yaml") else ".yml"
                    bucket = "playbooks"
                member_path = "payload/{}/{}{}".format(
                    bucket, asset_ref, suffix
                )
                suggested_path = "journeyman-imports/{}/{}{}".format(
                    project_slug, asset_ref, suffix
                )
                content = source.read_bytes()
                archive_files[member_path] = content
                assets.append(
                    {
                        "ref": asset_ref,
                        "type": asset_type,
                        "path": member_path,
                        "suggested_path": suggested_path,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )

            asset = next(item for item in assets if item["ref"] == asset_ref)
            step_data["payload"] = asset_ref
            if not internal:
                step_data["playbook"] = asset["suggested_path"]

    document["payload"] = assets
    metadata = document.setdefault("journeyman_export", {})
    metadata["container"] = "zip"
    metadata["manifest"] = "journeyman.yml"
    return archive_files


def _prune_unused_credential_requirements(document):
    used = set()
    for repository in document.get("repositories", []) or []:
        if repository.get("credential"):
            used.add(str(repository["credential"]))
    for inventory in document.get("inventories", []) or []:
        if inventory.get("credential"):
            used.add(str(inventory["credential"]))
    for project in document.get("projects", []) or []:
        used.update(str(ref) for ref in project.get("credentials", []) or [] if ref)
        for step in project.get("steps", []) or []:
            used.update(str(ref) for ref in step.get("credentials", []) or [] if ref)
    document["credentials_required"] = [
        item
        for item in document.get("credentials_required", []) or []
        if str(item.get("ref") or "") in used
    ]


def prepare_payload_import(document, archive_files, repository_name, payload_prefix=None):
    """Validate archive payload and bind it to one destination Repository."""

    assets = document.get("payload", []) or []
    if not assets:
        return []
    repository = _lookup_by_name(Repository, repository_name)
    if repository is None:
        raise ConfigPortabilityError(
            "Destination repository {!r} was not found.".format(repository_name)
        )

    prefix = Path(str(payload_prefix or "").strip()) if payload_prefix else None
    if prefix is not None and (prefix.is_absolute() or ".." in prefix.parts):
        raise ConfigPortabilityError("--payload-prefix must be a safe relative path.")

    by_ref = {}
    writes = []
    for asset in assets:
        ref = str(asset.get("ref") or "").strip()
        member = str(asset.get("path") or "").strip()
        if not ref or ref in by_ref:
            raise ConfigPortabilityError("JXF payload contains a missing or duplicate asset ref.")
        content = archive_files.get(member)
        if content is None:
            raise ConfigPortabilityError(
                "JXF payload asset {!r} is missing archive member {!r}.".format(ref, member)
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(asset.get("sha256") or ""):
            raise ConfigPortabilityError(
                "JXF payload asset {!r} failed SHA-256 validation.".format(ref)
            )
        if len(content) != int(asset.get("size", -1)):
            raise ConfigPortabilityError(
                "JXF payload asset {!r} failed size validation.".format(ref)
            )
        suggested = Path(str(asset.get("suggested_path") or ""))
        if suggested.is_absolute() or ".." in suggested.parts or not str(suggested):
            raise ConfigPortabilityError(
                "JXF payload asset {!r} has an unsafe suggested path.".format(ref)
            )
        destination = (prefix / suggested.name) if prefix else suggested
        destination_name = destination.as_posix()
        if any(existing_name == destination_name for existing_name, _ in writes):
            raise ConfigPortabilityError(
                "Multiple JXF payload assets resolve to destination path {!r}."
                .format(destination_name)
            )
        by_ref[ref] = destination_name
        writes.append((destination_name, content))

    for project in document.get("projects", []) or []:
        project["repository"] = repository.name
        for step in project.get("steps", []) or []:
            ref = step.pop("payload", None)
            if ref:
                if ref not in by_ref:
                    raise ConfigPortabilityError(
                        "Project {!r} references unknown payload asset {!r}."
                        .format(project.get("name"), ref)
                    )
                step["repository"] = repository.name
                step["playbook"] = by_ref[ref]

    # Repository metadata in an internal JXF describes the source installation,
    # not the destination. Never import it when an archive payload is rebound.
    document["repositories"] = []
    _prune_unused_credential_requirements(document)
    return writes


def _lookup_by_name(model, name):
    if not name:
        return None
    return model.query.filter_by(name=str(name)).first()


def _credential_requirements_by_ref(document):
    result = {}
    for requirement in document.get(
        "credentials_required",
        [],
    ) or []:
        ref = str(
            requirement.get("ref") or ""
        ).strip()
        if ref:
            result[ref] = requirement
    return result


def _resolve_credential_ref(
    ref,
    requirements_by_ref,
):
    if not ref:
        return None

    requirement = requirements_by_ref.get(
        str(ref)
    )
    if requirement is None:
        return None

    name = str(requirement.get("name") or "").strip()
    if not name:
        return None

    candidates = (
        Credential.query
        .filter_by(
            name=name,
            credential_type=str(
                requirement.get("type") or ""
            ),
        )
        .order_by(Credential.owner, Credential.id)
        .all()
    )

    if len(candidates) == 1:
        return candidates[0]

    return None



def _parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value))


_FORBIDDEN_JXF_KEYS = {
    "owner",
    "username",
    "password",
    "private_key",
    "private_key_data",
    "token",
    "secret",
    "encrypted_data",
    "credential_key_id",
    "api_secret",
    "api_secret_digest",
    "registration_token",
    "registration_token_digest",
    "permissions",
    "access_mode",
    "principal_name",
    "principal_type",
    "principal_object_guid",
    "principal_dn",
    "builtin_key",
    "security_scope",
    "allow_as_reaction",
}


def _scan_forbidden_jxf_fields(
    value,
    path="$",
):
    errors = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = "{}.{}".format(
                path,
                key,
            )
            if str(key) in _FORBIDDEN_JXF_KEYS:
                errors.append(
                    "Forbidden JXF field found: {}."
                    .format(child_path)
                )
            errors.extend(
                _scan_forbidden_jxf_fields(
                    child,
                    child_path,
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(
                _scan_forbidden_jxf_fields(
                    child,
                    "{}[{}]".format(
                        path,
                        index,
                    ),
                )
            )

    return errors


def _validate_trust_fields(document):
    errors = []

    for index, project in enumerate(
        document.get("projects", []) or []
    ):
        if "enabled" in project:
            errors.append(
                "Forbidden JXF field found: "
                "$.projects[{}].enabled."
                .format(index)
            )

        for schedule_index, schedule in enumerate(
            project.get("schedules", []) or []
        ):
            if "enabled" in schedule:
                errors.append(
                    "Forbidden JXF field found: "
                    "$.projects[{}].schedules[{}].enabled."
                    .format(
                        index,
                        schedule_index,
                    )
                )

    for index, package in enumerate(
        document.get("packages", []) or []
    ):
        if "enabled" in package:
            errors.append(
                "Forbidden JXF field found: "
                "$.packages[{}].enabled."
                .format(index)
            )

    return errors


def preflight_import(document, replace_existing=False):
    errors = []
    warnings = []

    errors.extend(
        _scan_forbidden_jxf_fields(
            document
        )
    )
    errors.extend(
        _validate_trust_fields(
            document
        )
    )

    metadata = document.get("journeyman_export")
    if not isinstance(metadata, dict):
        errors.append("Missing journeyman_export metadata.")
    elif int(metadata.get("format_version", -1)) != FORMAT_VERSION:
        errors.append(
            "Unsupported export format version: {!r}.".format(
                metadata.get("format_version")
            )
        )

    if metadata and metadata.get("contains_secret_material") not in (False, None):
        errors.append(
            "Refusing an export marked as containing secret material."
        )

    requirements = document.get(
        "credentials_required",
        [],
    ) or []
    requirements_by_ref = {}

    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            errors.append(
                "Credential requirement {} must be a mapping."
                .format(index)
            )
            continue

        allowed_keys = {
            "ref",
            "name",
            "type",
        }
        extra_keys = set(requirement) - allowed_keys
        if extra_keys:
            errors.append(
                "Credential requirement {} contains forbidden/unknown "
                "field(s): {}."
                .format(
                    index,
                    ", ".join(
                        sorted(extra_keys)
                    ),
                )
            )
            continue

        ref = str(requirement.get("ref") or "").strip()
        name = str(requirement.get("name") or "").strip()
        credential_type = str(requirement.get("type") or "").strip()

        if not ref or not credential_type:
            errors.append(
                "Credential requirement {} must contain ref and type."
                .format(index)
            )
            continue

        if ref in requirements_by_ref:
            errors.append(
                "Duplicate credential reference {!r}."
                .format(ref)
            )
            continue

        requirements_by_ref[ref] = requirement

        if name:
            candidates = (
                Credential.query
                .filter_by(
                    name=name,
                    credential_type=credential_type,
                )
                .order_by(Credential.owner, Credential.id)
                .all()
            )

            if not candidates:
                errors.append(
                    "Missing credential for ref {!r}: name={!r} type={!r}."
                    .format(
                        ref,
                        name,
                        credential_type,
                    )
                )
            elif len(candidates) > 1:
                errors.append(
                    "Ambiguous credential for ref {!r}: name={!r} type={!r} "
                    "matches {} local credentials. Rename/map credentials "
                    "before import."
                    .format(
                        ref,
                        name,
                        credential_type,
                        len(candidates),
                    )
                )
        else:
            warnings.append(
                "Credential ref {!r} (type={!r}) is not named in this "
                "portable JXF and will remain unbound after import."
                .format(ref, credential_type)
            )

    # Every credential reference used elsewhere must refer to a declared
    # anonymous requirement.
    def validate_ref(ref, path):
        if ref is None:
            return
        if not isinstance(ref, str):
            errors.append(
                "{} must contain a credential ref string."
                .format(path)
            )
            return
        if ref not in requirements_by_ref:
            errors.append(
                "{} references undeclared credential ref {!r}."
                .format(path, ref)
            )

    for index, repository in enumerate(
        document.get("repositories", []) or []
    ):
        validate_ref(
            repository.get("credential"),
            "$.repositories[{}].credential"
            .format(index),
        )

    for index, inventory in enumerate(
        document.get("inventories", []) or []
    ):
        validate_ref(
            inventory.get("credential"),
            "$.inventories[{}].credential"
            .format(index),
        )

    for project_index, project in enumerate(
        document.get("projects", []) or []
    ):
        for ref_index, ref in enumerate(
            project.get("credentials", []) or []
        ):
            validate_ref(
                ref,
                "$.projects[{}].credentials[{}]"
                .format(
                    project_index,
                    ref_index,
                ),
            )

        for step_index, step in enumerate(
            project.get("steps", []) or []
        ):
            for ref_index, ref in enumerate(
                step.get("credentials", []) or []
            ):
                validate_ref(
                    ref,
                    "$.projects[{}].steps[{}].credentials[{}]"
                    .format(
                        project_index,
                        step_index,
                        ref_index,
                    ),
                )

    runner_names = {
        str(value)
        for project in document.get("projects", []) or []
        for value in (
            project.get("runner"),
            project.get("default_runner"),
        )
        if value
    }

    for crew in document.get("runner_crews", []) or []:
        runner_names.update(
            str(name)
            for name in crew.get("runners", []) or []
        )

    for name in sorted(runner_names):
        if Runner.query.filter_by(name=name).first() is None:
            errors.append(
                "Missing runner {!r}; runner registrations and secrets "
                "are not portable."
                .format(name)
            )

    for inventory in document.get("inventories", []) or []:
        if (
            inventory.get("inventory_type") == "static"
            and not _lookup_by_name(
                Inventory,
                inventory.get("name"),
            )
        ):
            warnings.append(
                "Static inventory {!r} has no exported content and must "
                "be created manually before it can be used."
                .format(
                    inventory.get("name")
                )
            )

    if not replace_existing:
        collision_sets = (
            ("Repository", Repository, document.get("repositories", []) or []),
            ("Environment", Environment, document.get("environments", []) or []),
            ("Inventory", Inventory, document.get("inventories", []) or []),
            ("Runner crew", RunnerCrew, document.get("runner_crews", []) or []),
            ("Project", Project, document.get("projects", []) or []),
            ("Package", ProjectPackage, document.get("packages", []) or []),
        )

        for label, model, rows in collision_sets:
            for row in rows:
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                existing = _lookup_by_name(model, name)
                if existing is None:
                    continue

                # Static inventory content is intentionally non-portable. An
                # existing destination static inventory is therefore a local
                # dependency to reuse, not an object the JXF is allowed to
                # overwrite. Built-in environments are similarly destination
                # owned and may be referenced without modification.
                if (
                    model is Inventory
                    and row.get("inventory_type") == "static"
                    and existing.inventory_type == "static"
                ):
                    warnings.append(
                        "Static inventory {!r} already exists and will be reused "
                        "without modification.".format(name)
                    )
                    continue
                if (
                    model is Environment
                    and bool(row.get("is_builtin"))
                    and bool(existing.is_builtin)
                ):
                    warnings.append(
                        "Built-in environment {!r} already exists and will be "
                        "reused without modification.".format(name)
                    )
                    continue

                errors.append(
                    "{} {!r} already exists on the destination. Refusing to "
                    "silently replace local configuration; re-run the import "
                    "with explicit replacement enabled if this overwrite is "
                    "intentional.".format(label, name)
                )

    return {
        "errors": errors,
        "warnings": warnings,
        "credential_requirements": requirements_by_ref,
    }


def _set_attrs(obj, data, fields):
    for field in fields:
        if field in data:
            setattr(obj, field, data[field])


def _upsert_repository(data, counts, credential_requirements):
    obj = _lookup_by_name(Repository, data["name"])
    if obj is None:
        obj = Repository(name=data["name"])
        db.session.add(obj)
        counts["repositories"]["create"] += 1
    else:
        counts["repositories"]["update"] += 1
    _set_attrs(
        obj,
        data,
        ("description", "repository_type", "url", "directory_path", "default_branch"),
    )
    obj.repository_type = str(getattr(obj, "repository_type", "git") or "git")
    if obj.repository_type not in {"git", "directory"}:
        raise ConfigPortabilityError(
            "Repository {!r} has unsupported repository type {!r}.".format(
                obj.name, obj.repository_type
            )
        )
    if obj.repository_type == "directory":
        obj.url = ""
        obj.default_branch = "main"
        try:
            obj.directory_path = validate_directory_repository_path(
                obj.directory_path,
                repositories=Repository.query.filter_by(
                    repository_type="directory"
                ).all(),
                exclude_repository_id=obj.id,
            )
        except GitError as exc:
            raise ConfigPortabilityError(str(exc)) from exc
    else:
        obj.directory_path = ""
    credential = _resolve_credential_ref(
        data.get("credential"),
        credential_requirements,
    )
    obj.credential_id = (
        credential.id
        if credential
        else None
    )
    return obj


def _upsert_environment(data, counts):
    obj = _lookup_by_name(Environment, data["name"])
    if obj is None:
        if data.get("is_builtin"):
            raise ConfigPortabilityError(
                "Built-in environment {!r} does not exist on destination."
                .format(data["name"])
            )
        obj = Environment(name=data["name"], path=data.get("path") or "")
        db.session.add(obj)
        counts["environments"]["create"] += 1
    elif obj.is_builtin and data.get("is_builtin"):
        # Built-in environments belong to the destination installation. JXF
        # may reference them but must never rewrite their local definition.
        return obj
    else:
        counts["environments"]["update"] += 1

    if not obj.is_builtin:
        _set_attrs(
            obj,
            data,
            (
                "path",
                "enabled",
                "is_default",
                "is_managed",
                "python_interpreter",
                "ansible_spec",
                "ansible_config_path",
                "pip_requirements",
                "system_requirements",
                "collection_requirements",
            ),
        )
    return obj


def _inventory_config_for_import(data):
    inventory_type = data.get("inventory_type")
    portable = data.get("config") or {}
    config = {}

    if inventory_type == "satellite":
        config = {"organization": str(portable.get("organization") or "")}
    elif inventory_type == "zabbix":
        config = {
            "tag_name": str(portable.get("tag_name") or ""),
            "tag_value": str(portable.get("tag_value") or ""),
            "include_disabled": bool(portable.get("include_disabled", False)),
        }
    elif inventory_type == "netbox":
        config = {
            "status": str(portable.get("status") or "active"),
            "tag": str(portable.get("tag") or ""),
            "site": str(portable.get("site") or ""),
            "role": str(portable.get("role") or ""),
        }
    elif inventory_type == "lightspeed":
        config = {"tags": str(portable.get("tags") or "")}
    elif inventory_type == "ovirt":
        config = {
            "query_filter": portable.get("query_filter") if isinstance(portable.get("query_filter"), dict) else None,
            "hostname_preference": portable.get("hostname_preference") or ["fqdn", "name"],
        }
    elif inventory_type == "filtered":
        source = _lookup_by_name(Inventory, portable.get("source_inventory"))
        if source is None:
            raise ConfigPortabilityError(
                "Filtered inventory {!r} references missing source {!r}."
                .format(data.get("name"), portable.get("source_inventory"))
            )
        config = {
            "source_inventory_id": source.id,
            "include_groups": portable.get("include_groups", []) or [],
            "exclude_groups": portable.get("exclude_groups", []) or [],
        }
    elif inventory_type == "composite":
        source_ids = []
        for source_name in portable.get("source_inventories", []) or []:
            source = _lookup_by_name(Inventory, source_name)
            if source is None:
                raise ConfigPortabilityError(
                    "Composite inventory {!r} references missing source {!r}."
                    .format(data.get("name"), source_name)
                )
            source_ids.append(source.id)
        config = {
            "source_inventory_ids": source_ids,
            "normalize_hostnames": str(portable.get("normalize_hostnames") or "none"),
        }
    elif inventory_type == "static":
        existing = _lookup_by_name(Inventory, data.get("name"))
        if existing is not None:
            config = _load_inventory_config(existing)
        else:
            config = {"content": ""}

    append_domain = str(portable.get("append_domain") or "").strip()
    if append_domain:
        config["append_domain"] = append_domain
    return config


def _upsert_inventory_shell(data, counts, credential_requirements):
    obj = _lookup_by_name(Inventory, data["name"])
    created = obj is None
    if created:
        obj = Inventory(name=data["name"])
        db.session.add(obj)
        counts["inventories"]["create"] += 1
    elif (
        data.get("inventory_type") == "static"
        and obj.inventory_type == "static"
    ):
        # Static inventory contents are intentionally not portable. Existing
        # static inventories are destination-owned dependencies and are reused
        # exactly as they are.
        return obj
    else:
        counts["inventories"]["update"] += 1
    _set_attrs(
        obj,
        data,
        ("inventory_type", "endpoint", "verify_tls", "enabled"),
    )
    credential_ref = data.get("credential")
    credential = _resolve_credential_ref(
        credential_ref,
        credential_requirements,
    )
    if credential is not None:
        obj.credential_id = credential.id
    elif created or credential_ref is None:
        obj.credential_id = None
    obj.status = "never_synced"
    return obj


def _upsert_crew(data, counts):
    obj = _lookup_by_name(RunnerCrew, data["name"])
    if obj is None:
        obj = RunnerCrew(name=data["name"])
        db.session.add(obj)
        counts["runner_crews"]["create"] += 1
    else:
        counts["runner_crews"]["update"] += 1
    _set_attrs(obj, data, ("description", "enabled"))
    obj.runners = [
        Runner.query.filter_by(name=name).one()
        for name in data.get("runners", []) or []
    ]
    return obj


def _project_ref(model, name, label):
    if not name:
        return None
    obj = _lookup_by_name(model, name)
    if obj is None:
        raise ConfigPortabilityError(
            "{} {!r} was not found on the destination.".format(label, name)
        )
    return obj


def _upsert_project(data, counts, credential_requirements):
    if "concurrency_policy" not in data and "allow_concurrent_instances" in data:
        data = dict(data)
        data["concurrency_policy"] = (
            "unrestricted" if data.get("allow_concurrent_instances") else "exclusive"
        )

    obj = _lookup_by_name(Project, data["name"])
    if obj is None:
        obj = Project(name=data["name"])
        db.session.add(obj)
        counts["projects"]["create"] += 1
    else:
        counts["projects"]["update"] += 1

    _set_attrs(
        obj,
        data,
        (
            "description",
            "execution_type",
            "max_parallel_steps",
            "concurrency_policy",
            "oversight_required_between_all_steps",
            "runner_routing",
            "runner_site",
        ),
    )
    # JXF installs definitions; it never grants trust. An administrator must
    # explicitly review/enable the imported Project.
    obj.enabled = False
    obj.owner = "system"
    obj.security_scope = "private"
    obj.builtin_key = None

    obj.repository = _project_ref(Repository, data.get("repository"), "Repository")
    obj.inventory = _project_ref(Inventory, data.get("inventory"), "Inventory")
    obj.environment = _project_ref(Environment, data.get("environment"), "Environment")
    obj.runner = _project_ref(Runner, data.get("runner"), "Runner")
    obj.default_runner = _project_ref(Runner, data.get("default_runner"), "Runner")
    obj.default_runner_crew = _project_ref(
        RunnerCrew, data.get("default_runner_crew"), "Runner crew"
    )
    obj.credentials = [
        credential
        for credential in (
            _resolve_credential_ref(
                ref,
                credential_requirements,
            )
            for ref in data.get(
                "credentials",
                [],
            ) or []
        )
        if credential is not None
    ]

    obj.steps.clear()
    db.session.flush()
    for step_data in data.get("steps", []) or []:
        step = ProjectStep(
            project=obj,
            position=int(step_data["position"]),
            name=step_data.get("name") or "",
            playbook=step_data.get("playbook") or "",
        )
        _set_attrs(
            step,
            step_data,
            (
                "limit", "tags", "skip_tags", "verbosity", "check_mode", "remote_shell_become",
                "remote_shell_serial", "enabled", "continue_on_failure",
                "failure_only", "refresh_repository", "refresh_inventory_after",
                "oversight_after", "credentials_override",
            ),
        )
        step.repository = _project_ref(
            Repository, step_data.get("repository"), "Repository"
        )
        step.inventory = _project_ref(
            Inventory, step_data.get("inventory"), "Inventory"
        )
        step.environment = _project_ref(
            Environment, step_data.get("environment"), "Environment"
        )
        step.credentials = [
            credential
            for credential in (
                _resolve_credential_ref(
                    ref,
                    credential_requirements,
                )
                for ref in step_data.get(
                    "credentials",
                    [],
                ) or []
            )
            if credential is not None
        ]
        step.set_extra_vars(step_data.get("extra_vars", {}) or {})
        step.set_dependency_positions(step_data.get("depends_on", []) or [])
        obj.steps.append(step)

    if (
        data.get("oversight_required_between_all_steps")
        and not any(
            "oversight_after" in item
            for item in (data.get("steps", []) or [])
        )
    ):
        dependency_targets = {
            dependency
            for step in obj.steps
            for dependency in step.get_dependency_positions()
        }
        for step in obj.steps:
            step.oversight_after = step.position in dependency_targets

    obj.schedules.clear()
    for schedule_data in data.get("schedules", []) or []:
        schedule = ProjectSchedule(
            project=obj,
            name=schedule_data["name"],
            schedule_type=schedule_data.get("schedule_type") or "once",
            timezone_name=schedule_data.get("timezone_name") or "UTC",
            start_at=_parse_datetime(schedule_data.get("start_at")),
            end_at=_parse_datetime(schedule_data.get("end_at")),
            interval_minutes=schedule_data.get("interval_minutes"),
            weekdays=schedule_data.get("weekdays") or "",
            enabled=False,
            created_by="config-import",
        )
        obj.schedules.append(schedule)
    return obj


def _upsert_package(data, counts):
    project = _lookup_by_name(
        Project,
        data.get("project"),
    )
    if project is None:
        raise ConfigPortabilityError(
            "Package {!r} references missing Project {!r}."
            .format(
                data.get("name"),
                data.get("project"),
            )
        )

    obj = _lookup_by_name(
        ProjectPackage,
        data["name"],
    )
    if obj is None:
        obj = ProjectPackage(
            name=data["name"],
            project=project,
        )
        db.session.add(obj)
        counts["packages"]["create"] += 1
    else:
        counts["packages"]["update"] += 1
        obj.project = project

    _set_attrs(
        obj,
        data,
        (
            "description",
            "warning_message",
            "confirmation_required",
            "confirmation_message",
        ),
    )

    # JXF may install Package behaviour but may never grant access to it.
    obj.enabled = False
    obj.owner = "system"
    obj.access_mode = "restricted"
    obj.allow_as_reaction = False
    obj.builtin_key = None
    obj.permissions.clear()

    obj.set_fixed_vars(
        data.get("fixed_vars", {}) or {}
    )

    obj.inputs.clear()
    for input_data in data.get("inputs", []) or []:
        item = ProjectPackageInput(
            position=int(input_data["position"]),
            variable_name=input_data["variable_name"],
            label=(
                input_data.get("label")
                or input_data["variable_name"]
            ),
            help_text=input_data.get("help_text") or "",
            input_type=input_data.get("input_type") or "text",
            required=bool(
                input_data.get("required", False)
            ),
            is_secret=bool(
                input_data.get("is_secret", False)
            ),
            display_role=(
                input_data.get("display_role")
                or "normal"
            ),
            binding_type=(
                input_data.get("binding_type")
                or "extra_var"
            ),
            bind_to_inventory=bool(
                input_data.get(
                    "bind_to_inventory",
                    False,
                )
            ),
            inventory_binding_name=(
                input_data.get(
                    "inventory_binding_name"
                )
                or ""
            ),
        )
        item.set_default_value(
            None
            if item.is_secret
            else input_data.get(
                "default_value"
            )
        )
        item.set_choices(
            input_data.get(
                "choices",
                [],
            )
            or []
        )
        item.set_validation(
            input_data.get(
                "validation",
                {},
            )
            or {}
        )
        item.set_conditions(
            input_data.get(
                "conditions",
                {},
            )
            or {}
        )
        obj.inputs.append(item)

    obj.permissions.clear()
    return obj


def _empty_counts():
    return {
        name: {"create": 0, "update": 0}
        for name in (
            "repositories",
            "environments",
            "inventories",
            "runner_crews",
            "projects",
            "packages",
        )
    }


def import_configuration(document, dry_run=False, replace_existing=False):
    preflight = preflight_import(
        document,
        replace_existing=replace_existing,
    )
    if preflight["errors"]:
        raise ConfigPortabilityError(
            "Import preflight failed:\n- "
            + "\n- ".join(preflight["errors"])
        )

    counts = _empty_counts()
    credential_requirements = preflight[
        "credential_requirements"
    ]

    try:
        for data in document.get("repositories", []) or []:
            _upsert_repository(
                data,
                counts,
                credential_requirements,
            )
        for data in document.get("environments", []) or []:
            _upsert_environment(data, counts)
        db.session.flush()

        inventory_rows = document.get("inventories", []) or []
        # Create shells first so filtered/composite references can resolve.
        for data in inventory_rows:
            _upsert_inventory_shell(
                data,
                counts,
                credential_requirements,
            )
        db.session.flush()
        for data in inventory_rows:
            inventory = _lookup_by_name(Inventory, data["name"])
            inventory.config_json = json.dumps(
                _inventory_config_for_import(data),
                ensure_ascii=False,
                sort_keys=True,
            )

        for data in document.get("runner_crews", []) or []:
            _upsert_crew(data, counts)
        db.session.flush()

        for data in document.get("projects", []) or []:
            _upsert_project(
                data,
                counts,
                credential_requirements,
            )
        db.session.flush()

        for data in document.get("packages", []) or []:
            _upsert_package(data, counts)

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "counts": counts,
        "warnings": preflight["warnings"],
        "dry_run": bool(dry_run),
    }
