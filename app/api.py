"""Versioned REST API for Journeyman external automation clients."""

import json

from flask import Blueprint, g, jsonify, request

from app import db
from app.models import Credential, Inventory, Job, Project, ProjectPackage, ProjectSchedule, Reactor, Repository, SignalSource
from app.services.api_tokens import authenticate_api_token, token_expiry_warning
from app.services.job_cancellation import cancel_job
from app.services.job_rerun import JobRerunError, normalise_rerun_scope, rerun_job
from app.services.credential_configuration import (
    CredentialConfigurationError,
    configure_credential,
    credential_configuration_document,
    delete_credential,
)
from app.services.inventory_configuration import (
    InventoryConfigurationError,
    configure_inventory,
    delete_inventory,
)
from app.services.repository_configuration import (
    RepositoryConfigurationError,
    configure_repository,
    delete_repository,
)
from app.services.project_configuration import (
    ProjectConfigurationError,
    configure_project,
    delete_project,
)
from app.services.package_configuration import (
    PackageConfigurationError,
    configure_package,
    delete_package,
    package_configuration_document,
)
from app.services.schedule_configuration import (
    ScheduleConfigurationError,
    configure_schedule,
    delete_schedule,
    schedule_configuration_document,
)
from app.services.signal_source_configuration import (
    SignalSourceConfigurationError,
    configure_signal_source,
    delete_signal_source,
    signal_source_configuration_document,
)
from app.services.reactor_configuration import (
    ReactorConfigurationError,
    configure_reactor,
    delete_reactor,
    reactor_configuration_document,
)
from app.auth import can_launch_package
from app.services.project_execution_preview import (
    ProjectExecutionPreviewError,
    build_project_execution_preview,
)
from app.services.project_package_launch import (
    PackageLaunchError,
    prepare_package_launch,
)
from app.services.project_execution import ProjectExecutionQueueError, queue_project_execution

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _error(status, code, message):
    return jsonify({"error": {"code": code, "message": str(message)}}), status


def _bearer_token():
    value = str(request.headers.get("Authorization") or "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return ""
    return token.strip()


@bp.before_request
def authenticate_api_request():
    token = authenticate_api_token(_bearer_token())
    if token is None:
        return _error(401, "authentication_required", "A valid Journeyman API bearer token is required.")
    g.authenticated_username = token.username
    g.authenticated_display_name = token.username
    g.authenticated_role = token.role
    g.authenticated_group_names = []
    g.authenticated_group_object_guids = []
    g.authenticated_via = "api_token"
    g.api_token = token


@bp.after_request
def add_api_token_lifecycle_headers(response):
    token = getattr(g, "api_token", None)
    if token is None:
        return response
    warning = token_expiry_warning(token)
    response.headers["X-Journeyman-API-Token-Expires"] = token.expires_at.isoformat()
    if warning is not None:
        response.headers["X-Journeyman-API-Token-Expiry-Warning"] = "true"
        response.headers["Warning"] = (
            '299 Journeyman "API token expires within 30 days; create and deploy a replacement token."'
        )
    return response


def _package_document(package):
    return {
        "id": package.id,
        "name": package.name,
        "enabled": bool(package.enabled),
        "project_id": package.project_id,
        "project_name": package.project.name if package.project else None,
        "access_mode": package.access_mode,
        "inputs": [
            {
                "variable_name": item.variable_name,
                "label": item.label,
                "help_text": item.help_text or "",
                "type": item.input_type,
                "required": bool(item.required),
                "secret": bool(item.is_secret),
                "default": item.get_default_value(),
                "choices": item.get_choices(),
                "validation": item.get_validation(),
                "conditions": item.get_conditions(),
                "display_role": item.display_role,
            }
            for item in package.inputs
        ],
    }


def _package_form(package, supplied_inputs):
    if supplied_inputs is None:
        supplied_inputs = {}
    if not isinstance(supplied_inputs, dict):
        raise PackageLaunchError("Package inputs must be a JSON object.")

    declared = {item.variable_name: item for item in package.inputs}
    unknown = sorted(set(supplied_inputs) - set(declared))
    if unknown:
        raise PackageLaunchError(
            "Unknown Package input{}: {}.".format(
                "s" if len(unknown) != 1 else "",
                ", ".join(unknown),
            )
        )

    form = {}
    for variable_name, value in supplied_inputs.items():
        item = declared[variable_name]
        field_name = "package_value_{}".format(item.id)
        if item.input_type == "boolean":
            form[field_name] = "true" if bool(value) else "false"
        elif item.input_type == "choice":
            import json
            form[field_name] = json.dumps(value)
        elif value is None:
            form[field_name] = ""
        else:
            form[field_name] = str(value)
    return form

def _job_document(job):
    return {
        "id": job.id,
        "project_id": job.project_id,
        "project_name": job.project_name,
        "status": job.status,
        "requested_by": job.requested_by,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "exit_code": job.exit_code,
        "message": job.message or "",
        "steps": [
            {
                "position": step.position,
                "name": step.name,
                "status": step.status,
                "exit_code": step.exit_code,
            }
            for step in sorted(job.steps, key=lambda item: item.position)
        ],
    }


@bp.get("/projects")
def projects():
    name = str(request.args.get("name") or "").strip()
    query = Project.query
    if name:
        query = query.filter(Project.name == name)
    rows = query.order_by(Project.name.asc()).all()
    return jsonify({"projects": [
        {"id": row.id, "name": row.name, "enabled": bool(row.enabled)} for row in rows
    ]})


@bp.post("/projects/<int:project_id>/dispatch")
def dispatch_project(project_id):
    project = db.session.get(Project, project_id)
    if project is None:
        return _error(404, "not_found", "Project was not found.")
    if project.builtin_key and g.authenticated_role != "Administrator":
        return _error(403, "forbidden", "Administrator access is required for this Project.")
    try:
        job = queue_project_execution(
            project=project,
            requested_by=g.authenticated_username,
            message="Dispatched through Journeyman API.",
        )
    except ProjectExecutionQueueError as exc:
        return _error(409, "dispatch_rejected", str(exc))
    return jsonify({"job": _job_document(job)}), 202



@bp.get("/packages")
def packages():
    name = str(request.args.get("name") or "").strip()
    query = ProjectPackage.query
    if name:
        query = query.filter(ProjectPackage.name == name)
    rows = query.order_by(ProjectPackage.name.asc()).all()
    visible = [
        row for row in rows
        if can_launch_package(
            row,
            username=g.authenticated_username,
            group_names=g.authenticated_group_names,
            user_object_guid=None,
            group_object_guids=g.authenticated_group_object_guids,
            is_admin=(g.authenticated_role == "Administrator"),
        )
    ]
    return jsonify({"packages": [_package_document(row) for row in visible]})


@bp.post("/packages/<int:package_id>/dispatch")
def dispatch_package(package_id):
    package = db.session.get(ProjectPackage, package_id)
    if package is None:
        return _error(404, "not_found", "Package was not found.")
    if not can_launch_package(
        package,
        username=g.authenticated_username,
        group_names=g.authenticated_group_names,
        user_object_guid=None,
        group_object_guids=g.authenticated_group_object_guids,
        is_admin=(g.authenticated_role == "Administrator"),
    ):
        return _error(403, "forbidden", "You are not authorized to dispatch this Package.")

    document = request.get_json(silent=True) or {}
    try:
        form = _package_form(package, document.get("inputs", {}))
        errors, _fields, prepared = prepare_package_launch(package=package, form=form)
        if errors:
            raise PackageLaunchError(errors[0])
        preview = build_project_execution_preview(
            package.project,
            refresh_repositories=True,
            refresh_inventory_sources=True,
            step_limit_override=prepared.execution_data.step_limit or None,
            inventory_bindings=prepared.execution_data.inventory_bindings,
        )
        job = queue_project_execution(
            project=package.project,
            requested_by=g.authenticated_username,
            message='Dispatched from Package "{}" through Journeyman API.'.format(package.name),
            resolved_inventory_data=preview.resolved_inventory_data,
            package_execution=prepared.execution_data,
        )
    except (PackageLaunchError, ProjectExecutionPreviewError, ProjectExecutionQueueError) as exc:
        return _error(409, "dispatch_rejected", str(exc))

    return jsonify({"job": _job_document(job)}), 202


def _repository_document(repository):
    credential = None
    if repository.credential_id:
        from app.models import Credential
        row = db.session.get(Credential, repository.credential_id)
        credential = row.name if row else None
    return {
        "id": repository.id,
        "name": repository.name,
        "description": repository.description or "",
        "repository_type": repository.repository_type,
        "url": repository.url or "",
        "directory_path": repository.directory_path or "",
        "default_branch": repository.default_branch or "main",
        "credential": credential,
        "status": repository.status,
    }


def _administrator_required(resource="Repository"):
    if g.authenticated_role != "Administrator":
        return _error(403, "forbidden", f"Administrator access is required for {resource} configuration.")
    return None


@bp.get("/repositories")
def api_repositories():
    denied = _administrator_required()
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = Repository.query
    if name:
        query = query.filter(Repository.name == name)
    rows = query.order_by(Repository.name.asc()).all()
    return jsonify({"repositories": [_repository_document(row) for row in rows]})


@bp.put("/repositories/by-name")
def configure_repository_api():
    denied = _administrator_required()
    if denied:
        return denied
    document = request.get_json(silent=True) or {}
    try:
        result = configure_repository(document)
    except RepositoryConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "repository": _repository_document(result.repository),
    })


@bp.delete("/repositories/by-name")
def delete_repository_api():
    denied = _administrator_required()
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Repository name is required.")
    try:
        result = delete_repository(name)
    except RepositoryConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "repository": None,
    })



@bp.get("/credential-configurations")
def api_credential_configurations():
    denied = _administrator_required("Credential")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = Credential.query.filter(Credential.owner == g.authenticated_username)
    if name:
        query = query.filter(Credential.name == name)
    rows = query.order_by(Credential.name.asc()).all()
    return jsonify({"credentials": [credential_configuration_document(row) for row in rows]})


@bp.put("/credential-configurations/by-name")
def configure_credential_api():
    denied = _administrator_required("Credential")
    if denied:
        return denied
    try:
        result = configure_credential(
            request.get_json(silent=True) or {},
            owner=g.authenticated_username,
        )
    except CredentialConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "credential": credential_configuration_document(result.credential),
    })


@bp.delete("/credential-configurations/by-name")
def delete_credential_api():
    denied = _administrator_required("Credential")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Credential name is required.")
    try:
        result = delete_credential(name, owner=g.authenticated_username)
    except CredentialConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "credential": None})


def _inventory_configuration_document(inventory):
    try:
        config = json.loads(inventory.config_json or "{}")
    except (TypeError, ValueError):
        config = {}
    document = {
        "id": inventory.id,
        "name": inventory.name,
        "inventory_type": inventory.inventory_type,
        "enabled": bool(inventory.enabled),
        "verify_tls": bool(inventory.verify_tls),
        "status": inventory.status,
    }
    if inventory.credential:
        document["credential"] = inventory.credential.name
    if inventory.inventory_type == "satellite":
        document["organization"] = str(config.get("organization") or "")
    elif inventory.inventory_type == "static":
        document["content"] = str(config.get("content") or "")
    elif inventory.inventory_type == "zabbix":
        document.update({
            "endpoint": inventory.endpoint or "",
            "tag_name": str(config.get("tag_name") or ""),
            "tag_value": str(config.get("tag_value") or ""),
            "include_disabled": bool(config.get("include_disabled", False)),
        })
    elif inventory.inventory_type == "filtered":
        source = db.session.get(Inventory, config.get("source_inventory_id")) if config.get("source_inventory_id") else None
        document.update({
            "source_inventory": source.name if source else None,
            "include_groups": config.get("include_groups", []) or [],
            "exclude_groups": config.get("exclude_groups", []) or [],
        })
    elif inventory.inventory_type == "composite":
        names = []
        for source_id in config.get("source_inventory_ids", []) or []:
            source = db.session.get(Inventory, source_id)
            if source is not None:
                names.append(source.name)
        document["source_inventories"] = names
    return document


@bp.get("/inventory-configurations")
def api_inventory_configurations():
    denied = _administrator_required("Inventory")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = Inventory.query
    if name:
        query = query.filter(Inventory.name == name)
    rows = query.order_by(Inventory.name.asc()).all()
    return jsonify({"inventories": [_inventory_configuration_document(row) for row in rows]})


@bp.put("/inventory-configurations/by-name")
def configure_inventory_api():
    denied = _administrator_required("Inventory")
    if denied:
        return denied
    try:
        result = configure_inventory(request.get_json(silent=True) or {})
    except InventoryConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "inventory": _inventory_configuration_document(result.inventory),
    })


@bp.delete("/inventory-configurations/by-name")
def delete_inventory_api():
    denied = _administrator_required("Inventory")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Inventory name is required.")
    try:
        result = delete_inventory(name)
    except InventoryConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "inventory": None})


def _project_configuration_document(project):
    return {
        "id": project.id,
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
                "depends_on": [
                    project.steps[position - 1].name
                    for position in step.get_dependency_positions()
                    if 1 <= position <= len(project.steps)
                ],
                "enabled": bool(step.enabled),
            }
            for step in project.steps
        ],
    }


@bp.get("/project-configurations")
def api_project_configurations():
    denied = _administrator_required("Project")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = Project.query.filter(Project.builtin_key.is_(None))
    if name:
        query = query.filter(Project.name == name)
    rows = query.order_by(Project.name.asc()).all()
    return jsonify({"projects": [_project_configuration_document(row) for row in rows]})


@bp.put("/project-configurations/by-name")
def configure_project_api():
    denied = _administrator_required("Project")
    if denied:
        return denied
    try:
        result = configure_project(
            request.get_json(silent=True) or {},
            owner=g.authenticated_username,
        )
    except ProjectConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "project": _project_configuration_document(result.project),
    })


@bp.delete("/project-configurations/by-name")
def delete_project_api():
    denied = _administrator_required("Project")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Project name is required.")
    try:
        result = delete_project(name)
    except ProjectConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "project": None})

@bp.get("/package-configurations")
def api_package_configurations():
    denied = _administrator_required("Package")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = ProjectPackage.query.filter(ProjectPackage.builtin_key.is_(None))
    if name:
        query = query.filter(ProjectPackage.name == name)
    rows = query.order_by(ProjectPackage.name.asc()).all()
    return jsonify({"packages": [package_configuration_document(row) for row in rows]})


@bp.put("/package-configurations/by-name")
def configure_package_api():
    denied = _administrator_required("Package")
    if denied:
        return denied
    try:
        result = configure_package(
            request.get_json(silent=True) or {},
            owner=g.authenticated_username,
        )
    except PackageConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "package": package_configuration_document(result.package),
    })


@bp.delete("/package-configurations/by-name")
def delete_package_api():
    denied = _administrator_required("Package")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Package name is required.")
    try:
        result = delete_package(name)
    except PackageConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "package": None})


@bp.get("/schedule-configurations")
def api_schedule_configurations():
    denied = _administrator_required("Schedule")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    project_name = str(request.args.get("project") or "").strip()
    query = ProjectSchedule.query.join(Project)
    if name:
        query = query.filter(ProjectSchedule.name == name)
    if project_name:
        query = query.filter(Project.name == project_name)
    rows = query.order_by(Project.name.asc(), ProjectSchedule.name.asc()).all()
    return jsonify({"schedules": [schedule_configuration_document(row) for row in rows]})


@bp.put("/schedule-configurations/by-name")
def configure_schedule_api():
    denied = _administrator_required("Schedule")
    if denied:
        return denied
    try:
        result = configure_schedule(
            request.get_json(silent=True) or {},
            created_by=g.authenticated_username,
        )
    except ScheduleConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "schedule": schedule_configuration_document(result.schedule),
    })


@bp.delete("/schedule-configurations/by-name")
def delete_schedule_api():
    denied = _administrator_required("Schedule")
    if denied:
        return denied
    project_name = str(request.args.get("project") or "").strip()
    name = str(request.args.get("name") or "").strip()
    if not project_name or not name:
        return _error(400, "invalid_request", "Project and Schedule names are required.")
    try:
        result = delete_schedule(project_name, name)
    except ScheduleConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "schedule": None})


@bp.get("/signal-source-configurations")
def api_signal_source_configurations():
    denied = _administrator_required("Signal Source")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = SignalSource.query
    if name:
        query = query.filter(SignalSource.name == name)
    rows = query.order_by(SignalSource.name.asc()).all()
    return jsonify({"signal_sources": [signal_source_configuration_document(row) for row in rows]})


@bp.put("/signal-source-configurations/by-name")
def configure_signal_source_api():
    denied = _administrator_required("Signal Source")
    if denied:
        return denied
    try:
        result = configure_signal_source(request.get_json(silent=True) or {})
    except SignalSourceConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "signal_source": signal_source_configuration_document(result.source),
    })


@bp.delete("/signal-source-configurations/by-name")
def delete_signal_source_api():
    denied = _administrator_required("Signal Source")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Signal Source name is required.")
    try:
        result = delete_signal_source(name)
    except SignalSourceConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "signal_source": None})


@bp.get("/reactor-configurations")
def api_reactor_configurations():
    denied = _administrator_required("Reactor")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    query = Reactor.query
    if name:
        query = query.filter(Reactor.name == name)
    rows = query.order_by(Reactor.name.asc()).all()
    return jsonify({"reactors": [reactor_configuration_document(row) for row in rows]})


@bp.put("/reactor-configurations/by-name")
def configure_reactor_api():
    denied = _administrator_required("Reactor")
    if denied:
        return denied
    try:
        result = configure_reactor(request.get_json(silent=True) or {})
    except ReactorConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "reactor": reactor_configuration_document(result.reactor),
    })


@bp.delete("/reactor-configurations/by-name")
def delete_reactor_api():
    denied = _administrator_required("Reactor")
    if denied:
        return denied
    name = str(request.args.get("name") or "").strip()
    if not name:
        return _error(400, "invalid_request", "Reactor name is required.")
    try:
        result = delete_reactor(name)
    except ReactorConfigurationError as exc:
        return _error(409, "configuration_rejected", str(exc))
    return jsonify({"changed": result.changed, "message": result.message, "reactor": None})


@bp.get("/jobs/<int:job_id>")
def job_info(job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        return _error(404, "not_found", "Job was not found.")
    if g.authenticated_role != "Administrator" and job.requested_by != g.authenticated_username:
        return _error(403, "forbidden", "You are not authorized to view this Job.")
    return jsonify({"job": _job_document(job)})


@bp.post("/jobs/<int:job_id>/rerun")
def rerun_job_api(job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        return _error(404, "not_found", "Job was not found.")
    if g.authenticated_role != "Administrator" and job.requested_by != g.authenticated_username:
        return _error(403, "forbidden", "You are not authorized to rerun this Job.")
    payload = request.get_json(silent=True) or {}
    try:
        scope = normalise_rerun_scope(payload.get("scope"))
        result = rerun_job(
            job,
            requested_by=g.authenticated_username,
            scope=scope,
        )
    except JobRerunError as exc:
        return _error(409, "rerun_rejected", str(exc))
    return jsonify({
        "source_job_id": job.id,
        "rerun_scope": scope,
        "job": _job_document(result.job),
    }), 202


@bp.post("/jobs/<int:job_id>/cancel")
def cancel_job_api(job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        return _error(404, "not_found", "Job was not found.")
    if g.authenticated_role != "Administrator" and job.requested_by != g.authenticated_username:
        return _error(403, "forbidden", "You are not authorized to cancel this Job.")

    result = cancel_job(job, source="Journeyman API")
    return jsonify({
        "changed": result.changed,
        "message": result.message,
        "job": _job_document(job),
    })
