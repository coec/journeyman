"""Project administration, preview, and launch routes."""

from app.services.project_flowchart import (
    build_project_flowchart,
)
from app.services.builtin_automation import (
    ensure_builtin_admin_automation,
    is_builtin_project,
)

from app.services.costly_operation_rate_limit import costly_operation_rate_limit
from app.services.dispatch_progress import dispatch_progress_reporter
from app.services.configuration_deletion import (
    ConfigurationDeletionError,
    delete_project_with_job_history,
)
from app.services.user_preferences import get_or_create_user_preferences
from app.services.pagination import paginate_list, page_size_for_user
from app.services.environments import APPLICATION_ENVIRONMENT_NAME
from app.services.project_concurrency import (
    PROJECT_CONCURRENCY_POLICIES,
    CONCURRENCY_EXCLUSIVE,
)

from app.services.name_ordering import (
    reserved_name_ordering,
    reserved_name_sort_key,
    reserved_name_validation_error,
)

from app.routes import (
    Credential, Environment, Inventory, Project, Runner, RunnerCrew,
    ProjectExecutionPreviewError, ProjectExecutionQueueError, ProjectStep,
    Repository, _clean, _inventory_id_from_request, _project_steps_for_form,
    _project_steps_from_request, _repository_playbooks, _repository_scripts,
    _validate_project_steps, abort, bp,
    build_project_execution_preview, current_app, current_user_is_admin,
    current_username, db, ensure_builtin_environment, flash, json,
    queue_project_execution, redirect, render_template, request, url_for,
 )


def _optional_id(name):
    value = _clean(request.form.get(name))
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _execution_type():
    value = _clean(request.form.get("execution_type")) or "ansible"
    return value if value in {"ansible", "shell", "remote_shell"} else None


def _default_runner_destination():
    value = _clean(request.form.get("default_runner_destination"))
    if not value:
        # Backward compatibility with existing tests/clients that submit the
        # old exact-runner-only field.
        value = _clean(request.form.get("default_runner_id"))
    if not value or value == "local":
        return None, None
    if value.startswith("crew:"):
        try:
            return None, int(value.split(":", 1)[1])
        except (TypeError, ValueError):
            return -1, -1
    if value.startswith("runner:"):
        value = value.split(":", 1)[1]
    try:
        return int(value), None
    except (TypeError, ValueError):
        return -1, -1


def _parallel_step_limit():
    value = _clean(request.form.get("max_parallel_steps"))
    try:
        value = int(value or 4)
    except (TypeError, ValueError):
        return None
    return value


def _concurrency_policy():
    value = _clean(request.form.get("concurrency_policy")) or CONCURRENCY_EXCLUSIVE
    return value if value in PROJECT_CONCURRENCY_POLICIES else None


def _credential_ids(name):
    values = []
    for value in request.form.getlist(name):
        try:
            credential_id = int(value)
        except (TypeError, ValueError):
            continue
        if credential_id not in values:
            values.append(credential_id)
    return values






def _project_dispatch_readiness_issues(project):
    """Return inexpensive configuration issues that block Project dispatch."""

    issues = []
    steps = sorted(
        (step for step in project.steps if step.enabled),
        key=lambda step: step.position,
    )

    if not project.enabled:
        issues.append("This Project is disabled.")

    if not steps:
        issues.append("This Project has no enabled workflow steps.")
        return issues

    positions = {step.position for step in steps}
    dependency_map = {
        step.position: step.get_dependency_positions()
        for step in steps
    }
    for step in steps:
        for dependency in dependency_map[step.position]:
            if dependency not in positions:
                issues.append(
                    f"Step {step.position} references a dependency that does not exist."
                )
            elif dependency == step.position:
                issues.append(f"Step {step.position} cannot depend on itself.")

    visiting = set()
    visited = set()

    def visit(position):
        if position in visiting:
            return True
        if position in visited:
            return False
        visiting.add(position)
        for dependency in dependency_map.get(position, []):
            if dependency in dependency_map and visit(dependency):
                return True
        visiting.remove(position)
        visited.add(position)
        return False

    if any(visit(position) for position in dependency_map):
        issues.append("Workflow step dependencies contain a cycle.")

    for step in steps:
        label = step.name or f"Step {step.position}"
        if step.failure_only and not step.get_dependency_positions():
            issues.append(
                f'Step {step.position} "{label}" is failure-only but has no dependency.'
            )

        repository = step.effective_repository()
        if repository is None:
            issues.append(f'Step {step.position} "{label}" has no repository.')
        elif repository.status != "up_to_date":
            issues.append(
                f'Step {step.position} repository "{repository.name}" is not synchronized.'
            )

        if not step.playbook:
            artifact = (
                "script"
                if project.execution_type in {"shell", "remote_shell"}
                else "Ansible YAML file"
            )
            issues.append(
                f'Step {step.position} "{label}" has no {artifact} selected.'
            )

        environment = step.effective_environment()
        if environment is not None:
            if not environment.enabled:
                issues.append(
                    f'Step {step.position} environment "{environment.name}" is disabled.'
                )
            elif environment.validation_status != "passed":
                issues.append(
                    f'Step {step.position} environment "{environment.name}" has not passed validation.'
                )

        if project.execution_type != "shell":
            inventory = step.inventory or project.inventory
            if inventory is None:
                issues.append(
                    f'Step {step.position} "{label}" has no effective inventory.'
                )
            elif not inventory.enabled:
                issues.append(
                    f'Step {step.position} inventory "{inventory.name}" is disabled.'
                )

    return issues

def _direct_dispatch_block_reason(project):
    """Return the first reason this Project cannot be directly dispatched."""

    # A Package may expose required prompts that are only Package-level
    # launch conveniences (for example extra vars).  Merely wrapping a
    # Project in such a Package must not disable direct Project dispatch.
    # Inventory bindings are different: the Project inventory cannot resolve
    # without a value supplied by the wrapping Package, so direct dispatch is
    # genuinely invalid.
    for package in project.packages:
        if any(
            package_input.required and package_input.bind_to_inventory
            for package_input in package.inputs
        ):
            return (
                "This Project requires Package inventory inputs and can only "
                "be dispatched through a Package."
            )

    issues = _project_dispatch_readiness_issues(project)
    if issues:
        suffix = "" if len(issues) == 1 else f" ({len(issues)} issues)"
        return "Project is not ready to dispatch: {}{}".format(issues[0], suffix)

    return ""

def _project_clone_name(project_name):
    candidate = "{} (copy)".format(project_name)
    suffix = 2

    while Project.query.filter(Project.name == candidate).first() is not None:
        candidate = "{} (copy {})".format(project_name, suffix)
        suffix += 1

    return candidate


@bp.get("/projects")
def projects():
    is_admin = current_user_is_admin()
    if is_admin:
        ensure_builtin_admin_automation()

    preferences = get_or_create_user_preferences(current_username())
    query = Project.query
    if preferences.hide_disabled_projects:
        query = query.filter(Project.enabled.is_(True))
    if not is_admin:
        query = query.filter(Project.builtin_key.is_(None))

    rows = query.order_by(Project.name.asc()).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    rows = pagination.items

    project_flowcharts = {
        project.id: build_project_flowchart(
            project
        )
        for project in rows
        if len(project.steps) > 1
    }
    project_dispatch_block_reasons = {
        project.id: _direct_dispatch_block_reason(project)
        for project in rows
    }

    return render_template(
        "projects.html",
        projects=rows,
        project_flowcharts=project_flowcharts,
        project_dispatch_block_reasons=project_dispatch_block_reasons,
        disabled_projects_hidden=preferences.hide_disabled_projects,
        pagination=pagination,
    )


@bp.route("/projects/new", methods=["GET", "POST"])
def project_new():
    if not current_user_is_admin():
        abort(403)

    repositories = (
        Repository.query
        .filter(Repository.status == "up_to_date")
        .order_by(*reserved_name_ordering(Repository.name))
        .all()
    )

    inventories = (
        Inventory.query
        .filter(Inventory.enabled.is_(True))
        .order_by(*reserved_name_ordering(Inventory.name))
        .all()
    )

    credentials = (
        Credential.query
        .order_by(Credential.name.asc())
        .all()
    )
    available_credential_ids = { credential.id for credential in credentials}

    ensure_builtin_environment()
    environments = (
        Environment.query
        .filter(
            Environment.enabled.is_(True),
            Environment.name != APPLICATION_ENVIRONMENT_NAME,
        )
        .order_by(Environment.is_default.desc(), Environment.name.asc())
        .all()
    )

    remote_runners = (
        Runner.query
        .filter(
            Runner.enabled.is_(True),
            Runner.is_local.is_(False),
            Runner.runner_uuid.isnot(None),
            Runner.api_secret_digest != "",
        )
        .order_by(Runner.name.asc())
        .all()
    )

    runner_crews = (
        RunnerCrew.query
        .filter(RunnerCrew.enabled.is_(True))
        .order_by(RunnerCrew.name.asc())
        .all()
    )

    ansible_files_by_repository = {
        repository.id: _repository_playbooks(repository)
        for repository in repositories
    }
    shell_files_by_repository = {
        repository.id: _repository_scripts(repository)
        for repository in repositories
    }
    playbooks_by_repository = ansible_files_by_repository

    form_data = {
        "name": "",
        "execution_type": "ansible",
        "description": "",
        "inventory_id": None,
        "repository_id": None,
        "environment_id": None,
        "credential_ids": [],
        "max_parallel_steps": 4,
        "concurrency_policy": CONCURRENCY_EXCLUSIVE,
        "check_mode": False,
        "oversight_required_between_all_steps": False,
        "runner_routing": "local",
        "runner_site": "",
        "runner_id": None,
        "default_runner_id": None,
        "default_runner_crew_id": None,
        "enabled": True,
        "steps": [
            {
                "name": "",
                "repository_id": None,
                "environment_id": None,
                "playbook": "",
                "limit": "",
                "tags": "",
                "skip_tags": "",
                "extra_vars_yaml": "",
                "extra_vars": {},
                "verbosity": 0,
                "check_mode": False,
                "continue_on_failure": False,
                "failure_only": False,
                "refresh_repository": False,
                "refresh_inventory_after": False,
                "credentials_override": False,
                "dependency_positions": [],
                "enabled": True,
            }
        ],
        "inventory_id": None,
    }

    if request.method == "POST":
        selected_runner_id, selected_crew_id = _default_runner_destination()
        form_data = {
            "name": _clean(request.form.get("name")),
            "execution_type": _execution_type(),
            "description": _clean(
                request.form.get("description")
            ),
            "inventory_id": _inventory_id_from_request(),
            "repository_id": _optional_id("repository_id"),
            "environment_id": _optional_id("environment_id"),
            "credential_ids": _credential_ids("credential_ids"),
            "max_parallel_steps": _parallel_step_limit(),
            "concurrency_policy": _concurrency_policy(),
            "check_mode": request.form.get("check_mode") == "on",
            "oversight_required_between_all_steps": False,
            "runner_routing": "local",
            "runner_site": "",
            "runner_id": None,
            "default_runner_id": selected_runner_id,
            "default_runner_crew_id": selected_crew_id,
            "enabled": request.form.get("enabled") == "on",
            "steps": _project_steps_from_request(),
        }

        step_rows = form_data["steps"]
        project_check_mode = (
            form_data["execution_type"] == "ansible"
            and form_data["check_mode"]
        )
        for row in step_rows:
            row["check_mode"] = project_check_mode

        dependency_targets = {
            dependency
            for row in step_rows
            for dependency in row.get("dependency_positions", [])
        }
        for position, row in enumerate(step_rows, start=1):
            if position not in dependency_targets:
                row["oversight_after"] = False
        applicable = [
            row for position, row in enumerate(step_rows, start=1)
            if position in dependency_targets
        ]
        form_data["oversight_required_between_all_steps"] = bool(applicable) and all(
            row.get("oversight_after", False) for row in applicable
        )

        errors = []

        if not form_data["name"]:
            errors.append("Name is required.")
        else:
            reserved_error = reserved_name_validation_error(
                form_data["name"]
            )
            if reserved_error:
                errors.append(reserved_error)
        if form_data["execution_type"] is None:
            errors.append("Project type is invalid.")
        if form_data["concurrency_policy"] is None:
            errors.append("Project concurrency policy is invalid.")

        selected_default_runner = None
        selected_default_crew = None
        if form_data["default_runner_id"] == -1 or form_data["default_runner_crew_id"] == -1:
            errors.append("Default execution destination is invalid.")
        elif form_data["default_runner_id"] is not None:
            selected_default_runner = db.session.get(Runner, form_data["default_runner_id"])
            if (
                selected_default_runner is None
                or selected_default_runner.is_local
                or not selected_default_runner.enabled
                or not selected_default_runner.is_registered
            ):
                errors.append("Select an enabled registered default runner.")
        elif form_data["default_runner_crew_id"] is not None:
            selected_default_crew = db.session.get(RunnerCrew, form_data["default_runner_crew_id"])
            if selected_default_crew is None or not selected_default_crew.enabled:
                errors.append("Select an enabled default Runner Crew.")
            elif not selected_default_crew.runners:
                errors.append("The selected Runner Crew has no members.")

        form_data["runner_routing"] = (
            "remote_runner" if selected_default_runner is not None
            else "remote_crew" if selected_default_crew is not None
            else "local"
        )
        form_data["runner_id"] = (
            selected_default_runner.id if selected_default_runner is not None else None
        )

        playbooks_by_repository = (
            shell_files_by_repository
            if form_data["execution_type"] in {"shell", "remote_shell"}
            else ansible_files_by_repository
        )

        if form_data["execution_type"] == "shell":
            form_data["inventory_id"] = None
            for step_row in form_data["steps"]:
                step_row["inventory_id"] = None

        if (
            form_data["max_parallel_steps"] is None
            or not 1 <= form_data["max_parallel_steps"] <= 32
        ):
            errors.append("Maximum parallel steps must be between 1 and 32.")

        errors.extend(
            _validate_project_steps(
                form_data["steps"],
                playbooks_by_repository,
                available_credential_ids,
                default_repository_id=form_data["repository_id"],
                default_environment_id=form_data["environment_id"],
                default_credential_ids=form_data["credential_ids"],
                execution_type=form_data["execution_type"] or "ansible",
                dispatch_validation=False,
            )
        )

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "project_form.html",
                project=None,
                repositories=repositories,
                credentials=credentials,
                inventories=inventories,
                environments=environments,
                remote_runners=remote_runners,
                runner_crews=runner_crews,
                playbooks_by_repository=playbooks_by_repository,
                ansible_files_by_repository=ansible_files_by_repository,
                shell_files_by_repository=shell_files_by_repository,
                form_data=form_data,
            )

        project = Project(
            name=form_data["name"],
            execution_type=form_data["execution_type"],
            description=form_data["description"],
            inventory_id=form_data["inventory_id"],
            repository_id=form_data["repository_id"],
            environment_id=form_data["environment_id"],
            max_parallel_steps=form_data["max_parallel_steps"],
            concurrency_policy=form_data["concurrency_policy"],
            oversight_required_between_all_steps=(
                form_data["oversight_required_between_all_steps"]
            ),
            runner_routing=form_data["runner_routing"],
            runner_site=(form_data["runner_site"] if form_data["runner_routing"] == "remote_site" else ""),
            runner_id=(form_data["runner_id"] if form_data["runner_routing"] == "remote_runner" else None),
            default_runner_id=form_data["default_runner_id"],
            default_runner_crew_id=form_data["default_runner_crew_id"],
            credentials=[
                db.session.get(Credential, credential_id)
                for credential_id in form_data["credential_ids"]
                if db.session.get(Credential, credential_id) is not None
            ],
            enabled=form_data["enabled"],
        )

        db.session.add(project)

        for position, row in enumerate(
            form_data["steps"],
            start=1,
        ):
            selected_credentials = [
                db.session.get(Credential, credential_id)
                for credential_id in row["credential_ids"]
            ]
            selected_credentials = [
                credential
                for credential in selected_credentials
                if credential is not None
            ]

            project.steps.append(
                ProjectStep(
                    position=position,
                    name=row["name"] or f"Step {position}",
                    repository_id=row["repository_id"],
                    environment_id=row["environment_id"],
                    credentials=selected_credentials,
                    playbook=row["playbook"],
                    limit=row["limit"],
                    tags=row["tags"],
                    skip_tags=row["skip_tags"],
                    extra_vars_json=json.dumps(
                        row.get("extra_vars", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    verbosity=row["verbosity"],
                    check_mode=row.get("check_mode", False),
                    remote_shell_become=row.get("remote_shell_become", False),
                    remote_shell_serial=row.get("remote_shell_serial", 0),
                    continue_on_failure=row["continue_on_failure"],
                    failure_only=row["failure_only"],
                    refresh_repository=row["refresh_repository"],
                    refresh_inventory_after=row["refresh_inventory_after"],
                    oversight_after=row.get("oversight_after", False),
                    credentials_override=row["credentials_override"],
                    depends_on_json=json.dumps(row["dependency_positions"]),
                    enabled=row["enabled"],
                    inventory_id=row["inventory_id"],
                )
            )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to create Project"
            )

            flash(
                "Unable to create the project. The project name "
                "may already be in use.",
                "error",
            )

            return render_template(
                "project_form.html",
                project=None,
                repositories=repositories,
                credentials=credentials,
                inventories=inventories,
                environments=environments,
                remote_runners=remote_runners,
                runner_crews=runner_crews,
                playbooks_by_repository=playbooks_by_repository,
                ansible_files_by_repository=ansible_files_by_repository,
                shell_files_by_repository=shell_files_by_repository,
                form_data=form_data,
            )

        readiness_issues = _project_dispatch_readiness_issues(project)
        flash("Project created.", "success")
        if readiness_issues:
            flash(
                "Project saved, but it is not ready to dispatch: "
                + readiness_issues[0]
                + (f" ({len(readiness_issues)} issues)" if len(readiness_issues) > 1 else ""),
                "warning",
            )
        return redirect(url_for("main.projects"))

    return render_template(
        "project_form.html",
        project=None,
        repositories=repositories,
        credentials=credentials,
        inventories=inventories,
        environments=environments,
        remote_runners=remote_runners,
        runner_crews=runner_crews,
        playbooks_by_repository=playbooks_by_repository,
        ansible_files_by_repository=ansible_files_by_repository,
        shell_files_by_repository=shell_files_by_repository,
        form_data=form_data,
    )

@bp.route(
    "/projects/<int:project_id>/edit",
    methods=["GET", "POST"],
)
def project_edit(project_id):
    if not current_user_is_admin():
        abort(403)

    project = db.get_or_404(Project, project_id)

    repositories = (
        Repository.query
        .filter(Repository.status == "up_to_date")
        .order_by(*reserved_name_ordering(Repository.name))
        .all()
    )

    credentials = (
        Credential.query
        .order_by(Credential.name.asc())
        .all()
    )
    available_credential_ids = { credential.id for credential in credentials}

    ensure_builtin_environment()
    environments = (
        Environment.query
        .filter(
            Environment.enabled.is_(True),
            Environment.name != APPLICATION_ENVIRONMENT_NAME,
        )
        .order_by(Environment.is_default.desc(), Environment.name.asc())
        .all()
    )

    remote_runners = (
        Runner.query
        .filter(
            Runner.enabled.is_(True),
            Runner.is_local.is_(False),
            Runner.runner_uuid.isnot(None),
            Runner.api_secret_digest != "",
        )
        .order_by(Runner.name.asc())
        .all()
    )

    runner_crews = (
        RunnerCrew.query
        .filter(RunnerCrew.enabled.is_(True))
        .order_by(RunnerCrew.name.asc())
        .all()
    )

    # Keep repositories already used by this project available in the
    # edit form even if they are no longer currently synchronized.
    repository_ids = {repository.id for repository in repositories}

    if (
        project.repository is not None
        and project.repository.id not in repository_ids
    ):
        repositories.append(project.repository)
        repository_ids.add(project.repository.id)

    for step in project.steps:
        if (
            step.repository is not None
            and step.repository.id not in repository_ids
        ):
            repositories.append(step.repository)
            repository_ids.add(step.repository.id)

    repositories.sort(key=lambda repository: repository.name.lower())

    inventories = (
        Inventory.query
        .filter(Inventory.enabled.is_(True))
        .order_by(*reserved_name_ordering(Inventory.name))
        .all()
    )

    inventory_ids = {
        inventory.id
        for inventory in inventories
    }

    if (
        project.inventory is not None
        and project.inventory.id not in inventory_ids
    ):
        inventories.append(
            project.inventory
        )

        inventory_ids.add(
            project.inventory.id
        )

    for step in project.steps:
        if (
            step.inventory is not None
            and step.inventory.id not in inventory_ids
        ):
            inventories.append(
                step.inventory
            )

            inventory_ids.add(
                step.inventory.id
            )

    inventories.sort(
        key=lambda inventory: reserved_name_sort_key(
            inventory.name
        )
    )

    ansible_files_by_repository = {
        repository.id: _repository_playbooks(repository)
        for repository in repositories
    }
    shell_files_by_repository = {
        repository.id: _repository_scripts(repository)
        for repository in repositories
    }
    playbooks_by_repository = (
        shell_files_by_repository
        if project.execution_type in {"shell", "remote_shell"}
        else ansible_files_by_repository
    )

    form_data = {
        "name": project.name,
        "execution_type": project.execution_type or "ansible",
        "description": project.description,
        "inventory_id": project.inventory_id,
        "repository_id": project.repository_id,
        "environment_id": project.environment_id,
        "credential_ids": [credential.id for credential in project.credentials],
        "max_parallel_steps": project.max_parallel_steps,
        "concurrency_policy": project.concurrency_policy or CONCURRENCY_EXCLUSIVE,
        "check_mode": any(bool(step.check_mode) for step in project.steps),
        "oversight_required_between_all_steps": (
            project.oversight_required_between_all_steps
        ),
        "runner_routing": project.runner_routing or "local",
        "runner_site": project.runner_site or "",
        "runner_id": project.runner_id,
        "default_runner_id": (
            project.default_runner_id
            if project.default_runner_id is not None
            else (project.runner_id if project.runner_routing == "remote_runner" else None)
        ),
        "default_runner_crew_id": project.default_runner_crew_id,
        "enabled": project.enabled,
        "steps": _project_steps_for_form(project),
    }

    if request.method == "POST":
        selected_runner_id, selected_crew_id = _default_runner_destination()
        form_data = {
            "name": _clean(request.form.get("name")),
            "execution_type": _execution_type(),
            "description": _clean(
                request.form.get("description")
            ),
            "inventory_id": _inventory_id_from_request(),
            "repository_id": _optional_id("repository_id"),
            "environment_id": _optional_id("environment_id"),
            "credential_ids": _credential_ids("credential_ids"),
            "max_parallel_steps": _parallel_step_limit(),
            "concurrency_policy": _concurrency_policy(),
            "check_mode": request.form.get("check_mode") == "on",
            "oversight_required_between_all_steps": False,
            "runner_routing": "local",
            "runner_site": "",
            "runner_id": None,
            "default_runner_id": selected_runner_id,
            "default_runner_crew_id": selected_crew_id,
            "enabled": request.form.get("enabled") == "on",
            "steps": _project_steps_from_request(),
        }

        step_rows = form_data["steps"]
        project_check_mode = (
            form_data["execution_type"] == "ansible"
            and form_data["check_mode"]
        )
        for row in step_rows:
            row["check_mode"] = project_check_mode

        dependency_targets = {
            dependency
            for row in step_rows
            for dependency in row.get("dependency_positions", [])
        }
        for position, row in enumerate(step_rows, start=1):
            if position not in dependency_targets:
                row["oversight_after"] = False
        applicable = [
            row for position, row in enumerate(step_rows, start=1)
            if position in dependency_targets
        ]
        form_data["oversight_required_between_all_steps"] = bool(applicable) and all(
            row.get("oversight_after", False) for row in applicable
        )

        errors = []

        if not form_data["name"]:
            errors.append("Name is required.")
        else:
            reserved_error = reserved_name_validation_error(
                form_data["name"],
                existing_name=project.name,
            )
            if reserved_error:
                errors.append(reserved_error)

        if form_data["execution_type"] is None:
            errors.append("Project type is invalid.")
        if form_data["concurrency_policy"] is None:
            errors.append("Project concurrency policy is invalid.")

        selected_default_runner = None
        selected_default_crew = None
        if form_data["default_runner_id"] == -1 or form_data["default_runner_crew_id"] == -1:
            errors.append("Default execution destination is invalid.")
        elif form_data["default_runner_id"] is not None:
            selected_default_runner = db.session.get(Runner, form_data["default_runner_id"])
            if (
                selected_default_runner is None
                or selected_default_runner.is_local
                or not selected_default_runner.enabled
                or not selected_default_runner.is_registered
            ):
                errors.append("Select an enabled registered default runner.")
        elif form_data["default_runner_crew_id"] is not None:
            selected_default_crew = db.session.get(RunnerCrew, form_data["default_runner_crew_id"])
            if selected_default_crew is None or not selected_default_crew.enabled:
                errors.append("Select an enabled default Runner Crew.")
            elif not selected_default_crew.runners:
                errors.append("The selected Runner Crew has no members.")

        form_data["runner_routing"] = (
            "remote_runner" if selected_default_runner is not None
            else "remote_crew" if selected_default_crew is not None
            else "local"
        )
        form_data["runner_id"] = (
            selected_default_runner.id if selected_default_runner is not None else None
        )

        playbooks_by_repository = (
            shell_files_by_repository
            if form_data["execution_type"] in {"shell", "remote_shell"}
            else ansible_files_by_repository
        )

        if form_data["execution_type"] == "shell":
            form_data["inventory_id"] = None
            for step_row in form_data["steps"]:
                step_row["inventory_id"] = None

        if (
            form_data["max_parallel_steps"] is None
            or not 1 <= form_data["max_parallel_steps"] <= 32
        ):
            errors.append("Maximum parallel steps must be between 1 and 32.")

        errors.extend(
            _validate_project_steps(
                form_data["steps"],
                playbooks_by_repository,
                available_credential_ids,
                default_repository_id=form_data["repository_id"],
                default_environment_id=form_data["environment_id"],
                default_credential_ids=form_data["credential_ids"],
                execution_type=form_data["execution_type"] or "ansible",
                dispatch_validation=False,
            )
        )

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "project_form.html",
                project=project,
                repositories=repositories,
                credentials=credentials,
                inventories=inventories,
                environments=environments,
                remote_runners=remote_runners,
                runner_crews=runner_crews,
                playbooks_by_repository=playbooks_by_repository,
                ansible_files_by_repository=ansible_files_by_repository,
                shell_files_by_repository=shell_files_by_repository,
                form_data=form_data,
            )

        project.name = form_data["name"]
        project.execution_type = form_data["execution_type"]
        project.description = form_data["description"]
        project.inventory_id = form_data["inventory_id"]
        project.repository_id = form_data["repository_id"]
        project.environment_id = form_data["environment_id"]
        project.max_parallel_steps = form_data["max_parallel_steps"]
        project.concurrency_policy = form_data["concurrency_policy"]
        project.oversight_required_between_all_steps = (
            form_data["oversight_required_between_all_steps"]
        )
        project.runner_routing = form_data["runner_routing"]
        project.runner_site = (
            form_data["runner_site"]
            if form_data["runner_routing"] == "remote_site"
            else ""
        )
        project.runner_id = (
            form_data["runner_id"]
            if form_data["runner_routing"] == "remote_runner"
            else None
        )
        project.default_runner_id = form_data["default_runner_id"]
        project.default_runner_crew_id = form_data["default_runner_crew_id"]
        project.credentials = [
            db.session.get(Credential, credential_id)
            for credential_id in form_data["credential_ids"]
            if db.session.get(Credential, credential_id) is not None
        ]
        project.enabled = form_data["enabled"]

        # Explicitly delete and flush the existing rows before
        # inserting replacements. This avoids collisions with the
        # unique (project_id, position) constraint.
        for existing_step in list(project.steps):
            db.session.delete(existing_step)

        db.session.flush()

        for position, row in enumerate(
            form_data["steps"],
            start=1,
        ):
            selected_credentials = [
                db.session.get(Credential, credential_id)
                for credential_id in row["credential_ids"]
            ]
            selected_credentials = [
                credential
                for credential in selected_credentials
                if credential is not None
            ]

            project.steps.append(
                ProjectStep(
                    position=position,
                    name=row["name"] or f"Step {position}",
                    repository_id=row["repository_id"],
                    environment_id=row["environment_id"],
                    credentials=selected_credentials,
                    playbook=row["playbook"],
                    limit=row["limit"],
                    tags=row["tags"],
                    skip_tags=row["skip_tags"],
                    extra_vars_json=json.dumps(
                        row.get("extra_vars", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    verbosity=row["verbosity"],
                    check_mode=row.get("check_mode", False),
                    remote_shell_become=row.get("remote_shell_become", False),
                    remote_shell_serial=row.get("remote_shell_serial", 0),
                    enabled=row["enabled"],
                    continue_on_failure=row["continue_on_failure"],
                    failure_only=row["failure_only"],
                    refresh_repository=row["refresh_repository"],
                    refresh_inventory_after=row["refresh_inventory_after"],
                    oversight_after=row.get("oversight_after", False),
                    credentials_override=row["credentials_override"],
                    depends_on_json=json.dumps(row["dependency_positions"]),
                    inventory_id=row["inventory_id"],
                )
            )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to update Project %s",
                project_id,
            )

            flash(
                "Unable to update the project. The project name "
                "may already be in use.",
                "error",
            )

            return render_template(
                "project_form.html",
                project=project,
                repositories=repositories,
                credentials=credentials,
                inventories=inventories,
                environments=environments,
                remote_runners=remote_runners,
                runner_crews=runner_crews,
                playbooks_by_repository=playbooks_by_repository,
                ansible_files_by_repository=ansible_files_by_repository,
                shell_files_by_repository=shell_files_by_repository,
                form_data=form_data,
            )

        readiness_issues = _project_dispatch_readiness_issues(project)
        flash("Project updated.", "success")
        if readiness_issues:
            flash(
                "Project saved, but it is not ready to dispatch: "
                + readiness_issues[0]
                + (f" ({len(readiness_issues)} issues)" if len(readiness_issues) > 1 else ""),
                "warning",
            )
        return redirect(
            url_for("main.projects") + "#project-{}".format(project.id)
        )

    return render_template(
        "project_form.html",
        project=project,
        repositories=repositories,
        credentials=credentials,
        inventories=inventories,
        environments=environments,
        remote_runners=remote_runners,
        runner_crews=runner_crews,
        playbooks_by_repository=playbooks_by_repository,
        ansible_files_by_repository=ansible_files_by_repository,
        shell_files_by_repository=shell_files_by_repository,
        form_data=form_data,
    )

@bp.post("/projects/<int:project_id>/clone")
def project_clone(project_id):
    if not current_user_is_admin():
        abort(403)

    source = db.get_or_404(Project, project_id)

    if is_builtin_project(source):
        flash("Built-in Projects cannot be cloned.", "error")
        return redirect(url_for("main.projects"))

    cloned = Project(
        name=_project_clone_name(source.name),
        description=source.description,
        enabled=source.enabled,
        inventory_id=source.inventory_id,
        repository_id=source.repository_id,
        environment_id=source.environment_id,
        execution_type=source.execution_type,
        max_parallel_steps=source.max_parallel_steps,
        concurrency_policy=source.concurrency_policy,
        oversight_required_between_all_steps=(
            source.oversight_required_between_all_steps
        ),
        runner_routing=source.runner_routing,
        runner_site=source.runner_site,
        runner_id=source.runner_id,
        default_runner_id=source.default_runner_id,
        default_runner_crew_id=source.default_runner_crew_id,
        owner=current_username(),
        security_scope=source.security_scope,
    )
    cloned.credentials = list(source.credentials)

    for source_step in source.steps:
        cloned.steps.append(
            ProjectStep(
                position=source_step.position,
                name=source_step.name,
                repository_id=source_step.repository_id,
                inventory_id=source_step.inventory_id,
                environment_id=source_step.environment_id,
                playbook=source_step.playbook,
                limit=source_step.limit,
                tags=source_step.tags,
                skip_tags=source_step.skip_tags,
                extra_vars_json=source_step.extra_vars_json,
                verbosity=source_step.verbosity,
                check_mode=source_step.check_mode,
                remote_shell_become=source_step.remote_shell_become,
                remote_shell_serial=source_step.remote_shell_serial,
                enabled=source_step.enabled,
                continue_on_failure=source_step.continue_on_failure,
                failure_only=source_step.failure_only,
                refresh_repository=source_step.refresh_repository,
                refresh_inventory_after=source_step.refresh_inventory_after,
                oversight_after=source_step.oversight_after,
                credentials_override=source_step.credentials_override,
                depends_on_json=source_step.depends_on_json,
                credentials=list(source_step.credentials),
            )
        )

    db.session.add(cloned)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Unable to clone Project %s",
            project_id,
        )
        flash("Unable to clone the Project.", "error")
        return redirect(url_for("main.projects"))

    flash(
        'Project cloned as "{}". Review and rename it before use.'
        .format(cloned.name),
        "success",
    )

    return redirect(
        url_for(
            "main.project_edit",
            project_id=cloned.id,
        )
    )


@bp.post("/projects/<int:project_id>/delete")
def project_delete(project_id):
    if not current_user_is_admin():
        abort(403)

    project = db.get_or_404(Project, project_id)

    if is_builtin_project(project):
        flash("Built-in Projects cannot be deleted.", "error")
        return redirect(url_for("main.projects"))

    project_name = project.name
    try:
        job_ids, cleanup_errors = delete_project_with_job_history(project)
    except ConfigurationDeletionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.projects"))

    flash(
        'Project "{}" deleted with {} associated Job{}.'.format(
            project_name, len(job_ids), "" if len(job_ids) == 1 else "s"
        ),
        "success",
    )
    if cleanup_errors:
        flash(
            "The Project and Job history were deleted, but some Job filesystem "
            "output could not be removed. Check the Journeyman logs.",
            "warning",
        )

    return redirect(url_for("main.projects"))

@bp.get("/projects/<int:project_id>/run")
@costly_operation_rate_limit("execution_preview")
def project_run_preview(project_id):
    """
    Show the current effective targets before dispatching the Project.
    """

    project = db.get_or_404(
        Project,
        project_id,
    )

    if is_builtin_project(project) and not current_user_is_admin():
        abort(403)

    dispatch_block_reason = _direct_dispatch_block_reason(project)
    if dispatch_block_reason:
        flash(dispatch_block_reason, "error")
        return redirect(url_for("main.projects"))

    progress = dispatch_progress_reporter(
        request.headers.get("X-Journeyman-Dispatch-Progress", ""),
        current_username(),
        "Project — {}".format(project.name),
    )

    try:
        preview = build_project_execution_preview(
            project,
            refresh_repositories=True,
            refresh_inventory_sources=True,
            progress=progress,
        )

    except ProjectExecutionPreviewError as exc:
        progress.fail(str(exc))
        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.projects")
        )

    progress.done("Targets resolved — ready for review")

    return render_template(
        "project_run_preview.html",
        project=project,
        preview=preview,
        warning=None,
    )


@bp.post("/projects/<int:project_id>/run")
@costly_operation_rate_limit("execution_launch")
def project_run(project_id):
    """
    Confirm and dispatch the Project's currently previewed targets.
    """

    project = db.get_or_404(
        Project,
        project_id,
    )

    if is_builtin_project(project) and not current_user_is_admin():
        abort(403)

    dispatch_block_reason = _direct_dispatch_block_reason(project)
    if dispatch_block_reason:
        flash(dispatch_block_reason, "error")
        return redirect(url_for("main.projects"))

    progress = dispatch_progress_reporter(
        request.headers.get("X-Journeyman-Dispatch-Progress", ""),
        current_username(),
        "Project — {}".format(project.name),
    )
    progress("review", "Revalidating reviewed targets")

    try:
        preview = build_project_execution_preview(
            project,
            progress=progress,
        )

    except ProjectExecutionPreviewError as exc:
        progress.fail(str(exc))
        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.projects")
        )

    submitted_digest = str(
        request.form.get(
            "preview_digest",
            "",
        )
    ).strip()

    confirmed = (
        request.form.get(
            "confirm_targets"
        )
        == "yes"
    )

    if not confirmed:
        return render_template(
            "project_run_preview.html",
            project=project,
            preview=preview,
            warning=(
                "Review the target hosts and confirm "
                "before dispatching the Project."
            ),
        ), 400

    if (
        not submitted_digest
        or submitted_digest != preview.digest
    ):
        return render_template(
            "project_run_preview.html",
            project=project,
            preview=preview,
            warning=(
                "The Project configuration or resolved "
                "inventory changed after the previous preview. "
                "Review the updated targets before continuing."
            ),
        ), 409

    try:
        job = queue_project_execution(
            project=project,
            requested_by=current_username(),
            resolved_inventory_data=(
                preview.resolved_inventory_data
            ),
            progress=progress,
        )

    except ProjectExecutionQueueError as exc:
        progress.fail(str(exc))
        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.projects")
        )

    progress.done("Job #{} dispatched".format(job.id), job_id=job.id)

    flash(
        'Job #{} dispatched for "{}".'
        .format(
            job.id,
            project.name,
        ),
        "success",
    )

    return redirect(
        url_for(
            "main.job_detail",
            job_id=job.id,
        )
    )
