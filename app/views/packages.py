"""Project Package administration and launch routes."""

from dataclasses import replace

import app.routes as legacy_routes

from app.services.costly_operation_rate_limit import costly_operation_rate_limit
from app.services.dispatch_progress import dispatch_progress_reporter
from app.services.configuration_deletion import (
    ConfigurationDeletionError,
    delete_package_with_job_history,
)
from app.services.user_preferences import get_or_create_user_preferences
from app.services.ansible_view import package_configuration_yaml, dispatch_yaml
from app.services.pagination import paginate_list, page_size_for_user
from app.services.directory import DirectoryError, get_directory_client
from app.services.directory_settings import get_or_create_directory_settings

from app.services.name_ordering import (
    reserved_name_validation_error,
)
from app.services.builtin_automation import (
    REMOTE_RUNNER_BUILTIN_KEY,
    ensure_builtin_admin_automation,
    is_builtin_package,
)
from app.credential_types import CREDENTIAL_TYPE_MACHINE, CREDENTIAL_TYPE_URL
from app.models import Credential, Runner
from app.services.url_credentials import URLCredentialError, proxy_url_for_credential

from app.routes import (
    PACKAGE_ACCESS_AUTHENTICATED, PACKAGE_ACCESS_RESTRICTED,
    PackageLaunchError, PackageLaunchTokenError, Project,
    ProjectExecutionPreviewError, ProjectExecutionQueueError,
    ProjectPackage, VALID_PACKAGE_ACCESS_MODES, VARIABLE_NAME_PATTERN,
    _clean, abort, apply_package_input_rows, apply_package_permission_rows,
    bp, can_launch_package,
    create_package_launch_token, current_app, current_user_is_admin,
    current_username, db, flash, package_definition_digest,
    package_execution_from_token, package_input_rows_for_form,
    package_input_rows_from_request, package_launch_fields,
    package_permission_rows_for_form, package_permission_rows_from_request,
    package_principal_context, prepare_package_launch,
    read_package_launch_token, record_audit_event,
    redirect, render_template, request, url_for, validate_package_input_rows,
    validate_package_permission_rows, yaml,
)



def _value_contains_user_email(value):
    if isinstance(value, dict):
        return any(_value_contains_user_email(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_value_contains_user_email(item) for item in value)
    return isinstance(value, str) and "{{ user_email }}" in value


def _package_uses_user_email(package):
    if _value_contains_user_email(package.get_fixed_vars()):
        return True
    return any(
        _value_contains_user_email(item.get_default_value())
        for item in package.inputs
        if not item.is_secret
    )


def _package_runtime_values(package):
    """Resolve authenticated-user values required by this Package launch."""

    if not _package_uses_user_email(package):
        return {}

    username = current_username()
    try:
        settings = get_or_create_directory_settings()
        resolved = get_directory_client(settings).resolve_user_access(username)
    except DirectoryError as exc:
        raise PackageLaunchError(
            "Unable to resolve {{ user_email }} for {}: {}".format(
                username, exc
            )
        ) from exc

    email = str(resolved.user.mail or "").strip()
    if not email:
        raise PackageLaunchError(
            "Unable to resolve {{ user_email }}: the authenticated directory "
            "user {} has no email address.".format(username)
        )

    return {"user_email": email}


def _resolve_managed_runner_bootstrap_credential(package, prepared_launch):
    """Select the Machine credential used to bootstrap a remote runner host."""

    if (
        package.builtin_key != REMOTE_RUNNER_BUILTIN_KEY
        or prepared_launch is None
    ):
        return prepared_launch

    execution_vars = dict(prepared_launch.execution_data.execution_vars)
    raw_credential_id = execution_vars.get(
        "journeyman_bootstrap_credential_id", None
    )
    if raw_credential_id in (None, ""):
        return replace(
            prepared_launch,
            execution_data=replace(
                prepared_launch.execution_data,
                execution_vars=execution_vars,
                machine_credential_override_id=None,
            ),
        )

    try:
        credential_id = int(raw_credential_id)
    except (TypeError, ValueError) as exc:
        raise PackageLaunchError(
            "The selected bootstrap machine credential is invalid."
        ) from exc

    credential = db.session.get(Credential, credential_id)
    if (
        credential is None
        or credential.credential_type != CREDENTIAL_TYPE_MACHINE
    ):
        raise PackageLaunchError(
            "The selected bootstrap credential no longer exists or is not a "
            "Machine (Linux/UNIX) credential."
        )
    if credential.encrypted_data is None:
        raise PackageLaunchError(
            'Bootstrap credential "{}" has no stored secret data.'.format(
                credential.name
            )
        )

    return replace(
        prepared_launch,
        execution_data=replace(
            prepared_launch.execution_data,
            execution_vars=execution_vars,
            machine_credential_override_id=credential.id,
        ),
    )


def _resolve_managed_runner_proxy_credential(package, prepared_launch):
    """Resolve the selected URL credential into the transient pip proxy URL.

    The Package records only a credential identifier and display label. The
    decrypted proxy URL is added to encrypted Package launch data only for the
    execution itself and is never rendered back into the launch form.
    """

    if (
        package.builtin_key != REMOTE_RUNNER_BUILTIN_KEY
        or prepared_launch is None
    ):
        return prepared_launch

    execution_vars = dict(prepared_launch.execution_data.execution_vars)
    raw_credential_id = execution_vars.get(
        "journeyman_pip_proxy_credential_id", None
    )
    if raw_credential_id in (None, ""):
        return replace(
            prepared_launch,
            execution_data=replace(
                prepared_launch.execution_data,
                execution_vars=execution_vars,
            ),
        )

    try:
        credential_id = int(raw_credential_id)
    except (TypeError, ValueError) as exc:
        raise PackageLaunchError(
            "The selected pip proxy credential is invalid."
        ) from exc

    credential = db.session.get(Credential, credential_id)
    if (
        credential is None
        or credential.credential_type != CREDENTIAL_TYPE_URL
    ):
        raise PackageLaunchError(
            "The selected pip proxy credential no longer exists or is not a URL / API credential."
        )

    try:
        execution_vars["journeyman_pip_proxy_url"] = (
            proxy_url_for_credential(credential)
        )
    except URLCredentialError as exc:
        raise PackageLaunchError(
            "Unable to use pip proxy credential {}: {}".format(
                credential.name, exc
            )
        ) from exc

    return replace(
        prepared_launch,
        execution_data=replace(
            prepared_launch.execution_data,
            execution_vars=execution_vars,
        ),
    )


def _managed_runner_context(package, fields):
    """Prefill and lock the target host when managing an existing runner."""

    if package.builtin_key != REMOTE_RUNNER_BUILTIN_KEY:
        return fields

    raw_runner_id = str(request.args.get("runner_id") or "").strip()
    if not raw_runner_id:
        return fields

    try:
        runner_id = int(raw_runner_id)
    except ValueError:
        abort(400)

    runner = db.get_or_404(Runner, runner_id)
    if runner.is_local:
        abort(400)

    target_host = str(runner.hostname or runner.name or "").strip()
    if not target_host:
        abort(400)

    for field in fields:
        if field["variable_name"] == "journeyman_runner_host":
            field["value"] = target_host
            field["readonly"] = True
        elif field["variable_name"] == "journeyman_runner_name":
            field["value"] = str(runner.name or "").strip()
            field["readonly"] = True
        elif field["variable_name"] == "journeyman_bootstrap_credential_id":
            credential_id = runner.management_bootstrap_credential_id
            matching_choice = next(
                (
                    choice for choice in field.get("choices", [])
                    if str(choice.get("value")) == str(credential_id)
                ),
                None,
            )
            if matching_choice is not None:
                field["selected_choice_key"] = matching_choice["key"]
        elif field["variable_name"] == "journeyman_pip_proxy_required":
            field["checked"] = bool(runner.management_pip_proxy_required)
        elif field["variable_name"] == "journeyman_pip_proxy_credential_id":
            credential_id = runner.management_pip_proxy_credential_id
            matching_choice = next(
                (
                    choice for choice in field.get("choices", [])
                    if str(choice.get("value")) == str(credential_id)
                ),
                None,
            )
            if matching_choice is not None:
                field["selected_choice_key"] = matching_choice["key"]

    requested_action = str(request.args.get("action") or "").strip().lower()
    if requested_action:
        if requested_action != "update":
            abort(400)
        for field in fields:
            if field["variable_name"] != "journeyman_manage_action":
                continue
            matching_choice = next(
                (choice for choice in field.get("choices", []) if choice.get("value") == requested_action),
                None,
            )
            if matching_choice is None:
                abort(400)
            field["selected_choice_key"] = matching_choice["key"]
            break

    return fields


def _package_inventory_hostvars(package):
    """Return effective hostvars used to populate dynamic Package choices."""

    try:
        preview = legacy_routes.build_project_execution_preview(
            package.project,
            refresh_repositories=False,
            refresh_inventory_sources=False,
            step_limit_override="",
        )
    except ProjectExecutionPreviewError:
        return {}

    hostvars = {}
    for inventory_data in (preview.resolved_inventory_data or {}).values():
        if not isinstance(inventory_data, dict):
            continue
        meta = inventory_data.get("_meta")
        source_hostvars = meta.get("hostvars") if isinstance(meta, dict) else None
        if not isinstance(source_hostvars, dict):
            continue
        for hostname, values in source_hostvars.items():
            if isinstance(values, dict):
                hostvars[str(hostname)] = values
    return hostvars


def _step_limit_host_options(package, inventory_hostvars=None):
    """Return canonical inventory hostnames for editable Step Limit fields.

    The options are advisory only: Step Limit remains a free-form Ansible host
    pattern so advanced users can still enter a valid pattern manually.  Use
    the Project preview resolver with an explicit empty limit so the candidate
    list reflects the effective inventories without applying any configured
    per-step or Package limit.
    """

    if not any(
        item.binding_type == "step_limit"
        for item in package.inputs
    ):
        return []

    if inventory_hostvars:
        return sorted(str(host) for host in inventory_hostvars if str(host).strip())

    try:
        preview = legacy_routes.build_project_execution_preview(
            package.project,
            refresh_repositories=False,
            refresh_inventory_sources=False,
            step_limit_override="",
        )
    except ProjectExecutionPreviewError:
        return []

    return sorted(
        {
            str(host)
            for step in preview.steps
            for host in step.target_hosts
            if str(host).strip()
        }
    )


def _validate_managed_runner_target(package):
    """Prevent a crafted POST from changing a runner-specific locked target."""

    if package.builtin_key != REMOTE_RUNNER_BUILTIN_KEY:
        return None

    raw_runner_id = str(request.args.get("runner_id") or "").strip()
    if not raw_runner_id:
        return None

    try:
        runner_id = int(raw_runner_id)
    except ValueError:
        abort(400)

    runner = db.get_or_404(Runner, runner_id)
    if runner.is_local:
        abort(400)

    target_host = str(runner.hostname or runner.name or "").strip()
    host_input = next(
        (item for item in package.inputs if item.variable_name == "journeyman_runner_host"),
        None,
    )
    name_input = next(
        (item for item in package.inputs if item.variable_name == "journeyman_runner_name"),
        None,
    )
    if host_input is None or name_input is None:
        return "Manage Remote Runner is missing its target host or runner name input."

    submitted_host = str(
        request.form.get("package_value_{}".format(host_input.id)) or ""
    ).strip()
    submitted_name = str(
        request.form.get("package_value_{}".format(name_input.id)) or ""
    ).strip()
    if submitted_host != target_host:
        return "Target host cannot be changed when managing an existing runner."
    if submitted_name != str(runner.name or "").strip():
        return "Runner name cannot be changed when managing an existing runner."

    return None


PACKAGE_ACCESS_CHOICES = (
    (
        PACKAGE_ACCESS_RESTRICTED,
        "Restricted",
    ),
    (
        PACKAGE_ACCESS_AUTHENTICATED,
        "All authenticated users",
    ),
)


def _package_project_id_from_request():
    raw_value = _clean(
        request.form.get("project_id")
    )

    if not raw_value:
        return None

    try:
        project_id = int(raw_value)
    except (TypeError, ValueError):
        return None

    if project_id < 1:
        return None

    return project_id


def _package_fixed_vars_from_yaml(raw_value):
    raw_value = _clean(raw_value)

    if not raw_value:
        return {}

    try:
        values = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise ValueError(
            "Fixed variables contain invalid YAML: {}"
            .format(exc)
        ) from exc

    if values is None:
        return {}

    if not isinstance(values, dict):
        raise ValueError(
            "Fixed variables must be a YAML mapping."
        )

    for variable_name in values:
        if (
            not isinstance(variable_name, str)
            or not VARIABLE_NAME_PATTERN.fullmatch(
                variable_name
            )
        ):
            raise ValueError(
                "Invalid fixed variable name: {!r}"
                .format(variable_name)
            )

    return values


def _project_package_form_data(package=None):
    if request.method == "POST":
        return {
            "name": _clean(
                request.form.get("name")
            ),
            "description": _clean(
                request.form.get("description")
            ),
            "project_id": (
                _package_project_id_from_request()
            ),
            "enabled": (
                request.form.get("enabled")
                == "on"
            ),
            "allow_as_reaction": (
                request.form.get("allow_as_reaction")
                == "on"
            ),
            "access_mode": _clean(
                request.form.get("access_mode")
            ),
            "warning_message": _clean(
                request.form.get(
                    "warning_message"
                )
            ),
            "confirmation_required": (
                request.form.get(
                    "confirmation_required"
                )
                == "on"
            ),
            "confirmation_message": _clean(
                request.form.get(
                    "confirmation_message"
                )
            ),
            "fixed_vars_yaml": (
                request.form.get(
                    "fixed_vars_yaml",
                    "",
                )
            ).strip(),
            "inputs": (
                package_input_rows_from_request(
                    request.form
                )
            ),
            "permissions": (
                package_permission_rows_from_request(
                    request.form
                )
            ),
        }

    if package is None:
        return {
            "name": "",
            "description": "",
            "project_id": None,
            "enabled": True,
            "allow_as_reaction": False,
            "access_mode": (
                PACKAGE_ACCESS_RESTRICTED
            ),
            "warning_message": "",
            "confirmation_required": True,
            "confirmation_message": "",
            "fixed_vars_yaml": "{}",
            "inputs": [],
            "permissions": [],
        }

    fixed_vars_yaml = yaml.safe_dump(
        package.get_fixed_vars(),
        default_flow_style=False,
        sort_keys=True,
    ).strip()

    return {
        "name": package.name,
        "description": package.description,
        "project_id": package.project_id,
        "enabled": package.enabled,
        "allow_as_reaction": package.allow_as_reaction,
        "access_mode": package.access_mode,
        "warning_message": (
            package.warning_message
        ),
        "confirmation_required": (
            package.confirmation_required
        ),
        "confirmation_message": (
            package.confirmation_message
        ),
        "fixed_vars_yaml": (
            fixed_vars_yaml or "{}"
        ),
        "inputs": (
            package_input_rows_for_form(
                package
            )
        ),
        "permissions": (
            package_permission_rows_for_form(
                package
            )
        ),
    }


def _validate_project_package_form(
    form_data,
    package=None,
    allowed_principals=None,
):
    errors = []
    project = None
    fixed_vars = None
    normalised_inputs = []
    normalised_permissions = []

    if not form_data["name"]:
        errors.append(
            "Package name is required."
        )
    else:
        reserved_error = reserved_name_validation_error(
            form_data["name"],
            existing_name=(
                package.name if package is not None else None
            ),
        )
        if reserved_error:
            errors.append(reserved_error)

    duplicate_query = (
        ProjectPackage.query
        .filter(
            ProjectPackage.name
            == form_data["name"]
        )
    )

    if package is not None:
        duplicate_query = duplicate_query.filter(
            ProjectPackage.id != package.id
        )

    if (
        form_data["name"]
        and duplicate_query.first() is not None
    ):
        errors.append(
            "A Package with that name already exists."
        )

    project_id = form_data["project_id"]

    if project_id is None:
        errors.append(
            "A Project is required."
        )
    else:
        project = db.session.get(
            Project,
            project_id,
        )

        if project is None:
            errors.append(
                "The selected Project does not exist."
            )

    if (
        form_data["access_mode"]
        not in VALID_PACKAGE_ACCESS_MODES
    ):
        errors.append(
            "The selected access mode is invalid."
        )

    try:
        fixed_vars = (
            _package_fixed_vars_from_yaml(
                form_data["fixed_vars_yaml"]
            )
        )
    except ValueError as exc:
        errors.append(str(exc))

    (
        input_errors,
        normalised_inputs,
    ) = validate_package_input_rows(
        form_data["inputs"],
        fixed_vars or {},
    )

    errors.extend(
        input_errors
    )

    (
        permission_errors,
        normalised_permissions,
    ) = validate_package_permission_rows(
        form_data["permissions"],
        allowed_principals=allowed_principals,
    )

    errors.extend(
        permission_errors
    )

    return (
        errors,
        project,
        fixed_vars,
        normalised_inputs,
        normalised_permissions,
    )


@bp.get("/packages")
def packages():
    is_admin = current_user_is_admin()
    if is_admin:
        ensure_builtin_admin_automation()

    preferences = get_or_create_user_preferences(current_username())
    query = ProjectPackage.query
    if preferences.hide_disabled_packages:
        query = query.filter(ProjectPackage.enabled.is_(True))
    rows = query.order_by(ProjectPackage.name.asc()).all()

    launchable_package_ids = {
        package.id
        for package in rows
        if can_launch_package(package)
    }

    if not is_admin:
        rows = [
            package
            for package in rows
            if package.builtin_key is None
            and package.id in launchable_package_ids
        ]

    pagination = paginate_list(rows, page_size_for_user(current_username()))
    rows = pagination.items

    return render_template(
        "project_packages.html",
        packages=rows,
        is_admin=is_admin,
        launchable_package_ids=(launchable_package_ids),
        disabled_packages_hidden=preferences.hide_disabled_packages,
        pagination=pagination,
    )


def _launchable_package(package_id):
    package = db.get_or_404(
        ProjectPackage,
        package_id,
    )

    if is_builtin_package(package) and not current_user_is_admin():
        abort(403)

    if not can_launch_package(package):
        abort(403)

    return package


@bp.route(
    "/packages/<int:package_id>/launch",
    methods=["GET", "POST"],
)
@costly_operation_rate_limit("execution_preview")
def project_package_launch(package_id):
    package = _launchable_package(
        package_id
    )
    if package.builtin_key == REMOTE_RUNNER_BUILTIN_KEY:
        ensure_builtin_admin_automation()
        package = db.session.get(ProjectPackage, package.id)
    inventory_hostvars = _package_inventory_hostvars(package)

    try:
        runtime_values = _package_runtime_values(package)
    except PackageLaunchError as exc:
        return render_template(
            "project_package_launch.html",
            package=package,
            fields=_managed_runner_context(
                package,
                package_launch_fields(
                    package,
                    runtime_values={"user_email": ""},
                    inventory_hostvars=inventory_hostvars,
                ) if not _package_uses_user_email(package) else [],
            ),
            errors=[str(exc)],
            step_limit_host_options=(
                _step_limit_host_options(package, inventory_hostvars)
            ),
        ), 400

    if request.method == "GET":
        fields = _managed_runner_context(
            package,
            package_launch_fields(
                package,
                runtime_values=runtime_values,
                inventory_hostvars=inventory_hostvars,
            ),
        )
        return render_template(
            "project_package_launch.html",
            package=package,
            fields=fields,
            errors=[],
            step_limit_host_options=(
                _step_limit_host_options(package, inventory_hostvars)
            ),
        )

    progress = dispatch_progress_reporter(
        request.headers.get("X-Journeyman-Dispatch-Progress", ""),
        current_username(),
        "Package — {}".format(package.name),
    )
    progress("inputs", "Validating Package inputs")

    managed_target_error = _validate_managed_runner_target(package)

    (
        errors,
        fields,
        prepared_launch,
    ) = prepare_package_launch(
        package=package,
        form=request.form,
        runtime_values=runtime_values,
        inventory_hostvars=inventory_hostvars,
    )
    if managed_target_error:
        errors.append(managed_target_error)
    fields = _managed_runner_context(package, fields)

    if not errors:
        try:
            prepared_launch = _resolve_managed_runner_bootstrap_credential(
                package, prepared_launch
            )
            prepared_launch = _resolve_managed_runner_proxy_credential(
                package, prepared_launch
            )
        except PackageLaunchError as exc:
            errors.append(str(exc))

    if errors:
        progress.fail(errors[0])
        return render_template(
            "project_package_launch.html",
            package=package,
            fields=fields,
            errors=errors,
            step_limit_host_options=(
                _step_limit_host_options(package, inventory_hostvars)
            ),
        ), 400

    try:
        preview = (
            legacy_routes.build_project_execution_preview(
                package.project,
                refresh_repositories=True,
                refresh_inventory_sources=True,
                step_limit_override=(
                    prepared_launch
                    .execution_data
                    .step_limit
                    or None
                ),
                inventory_bindings=(
                    prepared_launch
                    .execution_data
                    .inventory_bindings
                ),
                progress=progress,
            )
        )

        launch_token = (
            create_package_launch_token(
                execution_data=(
                    prepared_launch
                    .execution_data
                ),
                requested_by=(
                    current_username()
                ),
                preview_digest=(
                    preview.digest
                ),
            )
        )

    except (
        ProjectExecutionPreviewError,
        PackageLaunchError,
    ) as exc:
        progress.fail(str(exc))
        return render_template(
            "project_package_launch.html",
            package=package,
            fields=fields,
            errors=[str(exc)],
            step_limit_host_options=(
                _step_limit_host_options(package, inventory_hostvars)
            ),
        ), 400

    progress.done("Targets resolved — ready for review")

    return render_template(
        "project_package_run_preview.html",
        package=package,
        package_execution=(
            prepared_launch.execution_data
        ),
        preview=preview,
        launch_token=launch_token,
        warning=None,
    )


@bp.post(
    "/packages/<int:package_id>/run"
)
@costly_operation_rate_limit("execution_launch")
def project_package_run(package_id):
    package = _launchable_package(
        package_id
    )
    if package.builtin_key == REMOTE_RUNNER_BUILTIN_KEY:
        ensure_builtin_admin_automation()
        package = db.session.get(ProjectPackage, package.id)

    username = current_username()
    progress = dispatch_progress_reporter(
        request.headers.get("X-Journeyman-Dispatch-Progress", ""),
        username,
        "Package — {}".format(package.name),
    )
    progress("review", "Revalidating reviewed Package values and targets")

    try:
        payload = read_package_launch_token(
            request.form.get(
                "launch_token",
                "",
            ),
            expected_package_id=(
                package.id
            ),
            expected_username=username,
        )

        if (
            payload.get(
                "package_definition_sha256"
            )
            != package_definition_digest(
                package
            )
        ):
            raise PackageLaunchTokenError(
                "The Package definition changed after it was "
                "reviewed. Enter the dispatch values again."
            )

        package_execution = (
            package_execution_from_token(
                package=package,
                payload=payload,
            )
        )

        preview = (
            legacy_routes.build_project_execution_preview(
                package.project,
                step_limit_override=(
                    package_execution.step_limit
                    or None
                ),
                inventory_bindings=(
                    package_execution.inventory_bindings
                ),
                progress=progress,
            )
        )

    except PackageLaunchTokenError as exc:
        progress.fail(str(exc))
        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for(
                "main.project_package_launch",
                package_id=package.id,
            )
        )

    except (
        PackageLaunchError,
        ProjectExecutionPreviewError,
    ) as exc:
        progress.fail(str(exc))
        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.packages")
        )

    if (
        payload.get("preview_digest")
        != preview.digest
    ):
        refreshed_token = (
            create_package_launch_token(
                execution_data=(
                    package_execution
                ),
                requested_by=username,
                preview_digest=(
                    preview.digest
                ),
            )
        )

        return render_template(
            "project_package_run_preview.html",
            package=package,
            package_execution=(
                package_execution
            ),
            preview=preview,
            launch_token=(
                refreshed_token
            ),
            warning=(
                "The Project configuration or resolved inventory "
                "changed after the previous preview. Review the "
                "updated targets before continuing."
            ),
        ), 409

    confirmed = (
        request.form.get(
            "confirm_targets"
        )
        == "yes"
    )

    if not confirmed:
        return render_template(
            "project_package_run_preview.html",
            package=package,
            package_execution=(
                package_execution
            ),
            preview=preview,
            launch_token=(
                create_package_launch_token(
                    execution_data=(
                        package_execution
                    ),
                    requested_by=username,
                    preview_digest=(
                        preview.digest
                    ),
                )
            ),
            warning=(
                "Review the Package values and target hosts "
                "before dispatching the Job."
            ),
        ), 400

    try:
        job = legacy_routes.queue_project_execution(
            project=package.project,
            requested_by=username,
            message=(
                'Dispatched from Package "{}".'
                .format(package.name)
            ),
            resolved_inventory_data=(
                preview.resolved_inventory_data
            ),
            package_execution=(
                package_execution
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
            url_for("main.packages")
        )

    progress.done("Job #{} dispatched".format(job.id), job_id=job.id)

    record_audit_event(
        "package.execute",
        result="queued",
        object_type="package",
        object_id=package.id,
        object_name=package.name,
        details={"job_id": job.id, "project_id": package.project_id},
    )
    flash(
        'Job #{} dispatched from Package "{}".'
        .format(
            job.id,
            package.name,
        ),
        "success",
    )

    return redirect(
        url_for(
            "main.job_detail",
            job_id=job.id,
        )
    )


@bp.route(
    "/packages/new",
    methods=["GET", "POST"],
)
def project_package_new():
    if not current_user_is_admin():
        abort(403)

    projects = (
        Project.query
        .order_by(Project.name.asc())
        .all()
    )

    principal_context = package_principal_context()

    form_data = _project_package_form_data()

    if request.method == "POST":
        (
            errors,
            project,
            fixed_vars,
            normalised_inputs,
            normalised_permissions,
        ) = _validate_project_package_form(
            form_data,
            allowed_principals=(
                principal_context["allowed"]
            ),
        )

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "project_package_form.html",
                package=None,
                projects=projects,
                access_choices=(
                    PACKAGE_ACCESS_CHOICES
                ),
                form_data=form_data,
                permission_choices=(
                    principal_context["choices"]
                ),
                permission_directory_error=(
                    principal_context["error"]
                ),
            )

        package = ProjectPackage(
            name=form_data["name"],
            description=(
                form_data["description"]
            ),
            project_id=project.id,
            enabled=form_data["enabled"],
            allow_as_reaction=form_data["allow_as_reaction"],
            owner=current_username(),
            access_mode=(
                form_data["access_mode"]
            ),
            warning_message=(
                form_data["warning_message"]
            ),
            confirmation_required=(
                form_data[
                    "confirmation_required"
                ]
            ),
            confirmation_message=(
                form_data[
                    "confirmation_message"
                ]
            ),
        )

        package.set_fixed_vars(
            fixed_vars
        )

        apply_package_input_rows(
            package,
            normalised_inputs,
            db.session,
        )

        apply_package_permission_rows(
            package,
            normalised_permissions,
            db.session,
        )

        db.session.add(package)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to create Project Package"
            )

            flash(
                "Unable to create the Package.",
                "error",
            )

            return render_template(
                "project_package_form.html",
                package=None,
                projects=projects,
                access_choices=(
                    PACKAGE_ACCESS_CHOICES
                ),
                form_data=form_data,
                permission_choices=(
                    principal_context["choices"]
                ),
                permission_directory_error=(
                    principal_context["error"]
                ),
            )

        flash(
            'Package "{}" created.'
            .format(package.name),
            "success",
        )

        return redirect(
            url_for("main.packages")
        )

    return render_template(
        "project_package_form.html",
        package=None,
        projects=projects,
        access_choices=(
            PACKAGE_ACCESS_CHOICES
        ),
        form_data=form_data,
        permission_choices=(
            principal_context["choices"]
        ),
        permission_directory_error=(
            principal_context["error"]
        ),
    )


def _package_ansible_context(package):
    if package.builtin_key is not None:
        abort(404)

    return {
        "resource_kind": "Package",
        "resource_name": package.name,
        "back_url": url_for("main.packages"),
    }


@bp.get("/packages/<int:package_id>/ansible")
def project_package_show_ansible(package_id):
    if not current_user_is_admin():
        abort(403)

    package = db.get_or_404(ProjectPackage, package_id)
    _package_ansible_context(package)
    return redirect(
        url_for(
            "main.project_package_show_ansible_configuration",
            package_id=package.id,
        )
    )


@bp.get("/packages/<int:package_id>/ansible/configuration")
def project_package_show_ansible_configuration(package_id):
    if not current_user_is_admin():
        abort(403)

    package = db.get_or_404(ProjectPackage, package_id)
    context = _package_ansible_context(package)
    return render_template(
        "show_ansible.html",
        ansible_kind="Configuration",
        ansible_yaml=package_configuration_yaml(package),
        ansible_note=None,
        **context
    )


@bp.get("/packages/<int:package_id>/ansible/operation")
def project_package_show_ansible_operation(package_id):
    if not current_user_is_admin():
        abort(403)

    package = db.get_or_404(ProjectPackage, package_id)
    context = _package_ansible_context(package)
    return render_template(
        "show_ansible.html",
        ansible_kind="Operation",
        ansible_yaml=dispatch_yaml("package", package.name),
        ansible_note=(
            "Package inputs are runtime values and are not included here. "
            "Add an inputs mapping to the dispatch task when this Package "
            "requires user-supplied values."
        ),
        **context
    )


@bp.route(
    "/packages/<int:package_id>/edit",
    methods=["GET", "POST"],
)
def project_package_edit(package_id):
    if not current_user_is_admin():
        abort(403)

    package = db.get_or_404(
        ProjectPackage,
        package_id,
    )

    projects = (
        Project.query
        .order_by(Project.name.asc())
        .all()
    )

    principal_context = package_principal_context()

    form_data = _project_package_form_data(
        package
    )

    if request.method == "POST":
        (
            errors,
            project,
            fixed_vars,
            normalised_inputs,
            normalised_permissions,
        ) = _validate_project_package_form(
            form_data,
            package=package,
            allowed_principals=(
                principal_context["allowed"]
            ),
        )

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "project_package_form.html",
                package=package,
                projects=projects,
                access_choices=(
                    PACKAGE_ACCESS_CHOICES
                ),
                form_data=form_data,
                permission_choices=(
                    principal_context["choices"]
                ),
                permission_directory_error=(
                    principal_context["error"]
                ),
            )

        package.name = form_data["name"]
        package.description = (
            form_data["description"]
        )
        package.project_id = project.id
        package.enabled = form_data["enabled"]
        package.allow_as_reaction = form_data["allow_as_reaction"]
        package.access_mode = (
            form_data["access_mode"]
        )
        package.warning_message = (
            form_data["warning_message"]
        )
        package.confirmation_required = (
            form_data[
                "confirmation_required"
            ]
        )
        package.confirmation_message = (
            form_data[
                "confirmation_message"
            ]
        )

        package.set_fixed_vars(
            fixed_vars
        )

        apply_package_input_rows(
            package,
            normalised_inputs,
            db.session,
        )

        apply_package_permission_rows(
            package,
            normalised_permissions,
            db.session,
        )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to update Project Package %s",
                package_id,
            )

            flash(
                "Unable to update the Package.",
                "error",
            )

            return render_template(
                "project_package_form.html",
                package=package,
                projects=projects,
                access_choices=(
                    PACKAGE_ACCESS_CHOICES
                ),
                form_data=form_data,
                permission_choices=(
                    principal_context["choices"]
                ),
                permission_directory_error=(
                    principal_context["error"]
                ),
            )

        flash(
            'Package "{}" updated.'
            .format(package.name),
            "success",
        )

        return redirect(
            url_for("main.packages")
        )

    return render_template(
        "project_package_form.html",
        package=package,
        projects=projects,
        access_choices=(
            PACKAGE_ACCESS_CHOICES
        ),
        form_data=form_data,
        permission_choices=(
            principal_context["choices"]
        ),
        permission_directory_error=(
            principal_context["error"]
        ),
    )


@bp.post(
    "/packages/<int:package_id>/delete"
)
def project_package_delete(package_id):
    if not current_user_is_admin():
        abort(403)

    package = db.get_or_404(
        ProjectPackage,
        package_id,
    )

    if is_builtin_package(package):
        flash("Built-in Packages cannot be deleted.", "error")
        return redirect(url_for("main.packages"))

    package_name = package.name
    try:
        job_ids, cleanup_errors = delete_package_with_job_history(package)
    except ConfigurationDeletionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.packages"))

    flash(
        'Package "{}" deleted with {} associated Job{}.'.format(
            package_name, len(job_ids), "" if len(job_ids) == 1 else "s"
        ),
        "success",
    )
    if cleanup_errors:
        flash(
            "The Package and Job history were deleted, but some Job filesystem "
            "output could not be removed. Check the Journeyman logs.",
            "warning",
        )

    return redirect(url_for("main.packages"))
