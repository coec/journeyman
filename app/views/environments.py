"""Execution-environment administration routes."""

from app import routes as legacy_routes
from pathlib import Path as FilesystemPath
import re

from app.services.pagination import paginate_list, page_size_for_user
from app.models import Credential, Environment, Runner
from app.credential_types import CREDENTIAL_TYPE_URL
from app.services.url_credentials import URLCredentialError, proxy_url_for_credential
from app.services.environments import APPLICATION_ENVIRONMENT_NAME
from app.services.runner_environment_sync import (
    RunnerEnvironmentSyncError,
    environment_sync_rows,
    is_syncable_environment,
    queue_environment_sync,
)
from app.services.costly_operation_rate_limit import (
    check_and_record_costly_operation,
    costly_operation_rate_limit,
)

from app.routes import (
    EnvironmentBuildError, Path, _clean, abort,
    allowed_python_interpreters, bp, current_user_is_admin, current_username, db,
    delete_managed_environment_files, ensure_builtin_environment, flash,
    managed_environment_path,
    record_audit_event, redirect, render_template, request, url_for,
    validate_environment,
)


DEFAULT_ANSIBLE_CONFIG_PATH = "/etc/ansible/ansible.cfg"
ANSIBLE_CONFIG_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


def _ansible_config_path_from_form(errors):
    value = _clean(request.form.get("ansible_config_path")) or DEFAULT_ANSIBLE_CONFIG_PATH
    path = FilesystemPath(value)

    if not path.is_absolute():
        errors.append("Ansible configuration path must be an absolute path.")
        return value

    if not ANSIBLE_CONFIG_PATH_RE.fullmatch(value):
        errors.append(
            "Ansible configuration path contains unsupported characters. "
            "Use letters, numbers, '/', '.', '_' or '-'."
        )

    if ".." in path.parts:
        errors.append("Ansible configuration path must not contain '..' components.")

    if path.suffix.lower() != ".cfg":
        errors.append("Ansible configuration path must end in .cfg.")

    return value


def _environment_proxy_credentials():
    credentials = (
        Credential.query
        .filter_by(credential_type=CREDENTIAL_TYPE_URL)
        .order_by(Credential.name.asc())
        .all()
    )
    compatible = []
    for credential in credentials:
        try:
            proxy_url_for_credential(credential)
        except URLCredentialError:
            continue
        compatible.append(credential)
    return compatible


def _proxy_credential_from_form(errors):
    raw = _clean(request.form.get("proxy_credential_id"))
    if not raw:
        return None
    try:
        credential_id = int(raw)
    except (TypeError, ValueError):
        errors.append("Build proxy credential is invalid.")
        return None
    credential = db.session.get(Credential, credential_id)
    if credential is None or credential.credential_type != CREDENTIAL_TYPE_URL:
        errors.append("Build proxy credential must be a URL / API credential.")
        return None
    try:
        proxy_url_for_credential(credential)
    except URLCredentialError as exc:
        errors.append(str(exc))
        return None
    return credential

@bp.route("/environments", methods=["GET", "POST"])
def environments():
    if not current_user_is_admin():
        abort(403)

    ensure_builtin_environment()

    if request.method == "POST":
        name = _clean(request.form.get("name"))
        path = _clean(request.form.get("path"))
        errors = []
        ansible_config_path = _ansible_config_path_from_form(errors)
        proxy_credential = _proxy_credential_from_form(errors)
        if not name:
            errors.append("Environment name is required.")
        if not path or not Path(path).is_absolute():
            errors.append("Environment path must be an absolute path.")
        if Environment.query.filter_by(name=name).first():
            errors.append("An environment with that name already exists.")
        if Environment.query.filter_by(path=path).first():
            errors.append("That virtual-environment path is already registered.")
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("main.environment_new"))

        environment = Environment(
            name=name, path=path, enabled=True,
            ansible_config_path=ansible_config_path,
            proxy_credential=proxy_credential,
        )
        db.session.add(environment)
        db.session.commit()
        if not validate_environment(environment):
            db.session.delete(environment)
            db.session.commit()
            flash(environment.validation_message or "Environment validation failed.", "error")
            return redirect(url_for("main.environment_new"))

        pip_requirements = request.form.get("pip_requirements") or ""
        system_requirements = request.form.get("system_requirements") or ""
        collections = request.form.get("collections") or ""
        if pip_requirements.strip() or system_requirements.strip() or collections.strip():
            try:
                legacy_routes.prepare_registered_environment_update(
                    environment,
                    pip_requirements=pip_requirements,
                    system_requirements=system_requirements,
                    collections=collections,
                )
            except EnvironmentBuildError as exc:
                db.session.delete(environment)
                db.session.commit()
                flash(str(exc), "error")
                return redirect(url_for("main.environment_new"))
            message = "Environment registered; dependency update queued."
        else:
            message = "Environment registered and validated."
        record_audit_event("environment.created", object_type="environment", object_id=environment.id, object_name=environment.name, details={"path": environment.path})
        flash(message, "success")
        return redirect(url_for("main.environments"))

    rows = Environment.query.order_by(Environment.is_default.desc(), Environment.name.asc()).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    return render_template(
        "environments.html",
        environments=pagination.items,
        pagination=pagination,
    )


@bp.get("/environments/new")
def environment_new():
    if not current_user_is_admin():
        abort(403)

    return render_template(
        "environment_new.html",
        python_interpreters=allowed_python_interpreters(),
        proxy_credentials=_environment_proxy_credentials(),
    )


@bp.post("/environments/create")
@costly_operation_rate_limit("environment_build")
def environment_create_managed():
    if not current_user_is_admin():
        abort(403)

    name = _clean(request.form.get("name"))
    python_interpreter = _clean(request.form.get("python_interpreter"))
    ansible_spec = _clean(request.form.get("ansible_spec")) or "ansible-core"
    pip_requirements = request.form.get("pip_requirements") or ""
    system_requirements = request.form.get("system_requirements") or ""
    collections = request.form.get("collections") or ""
    errors = []
    ansible_config_path = _ansible_config_path_from_form(errors)
    proxy_credential = _proxy_credential_from_form(errors)
    if not name:
        errors.append("Environment name is required.")
    if Environment.query.filter_by(name=name).first():
        errors.append("An environment with that name already exists.")

    path = None
    if not errors:
        try:
            path = managed_environment_path(name)
        except EnvironmentBuildError as exc:
            errors.append(str(exc))
    if path is not None and Environment.query.filter_by(path=str(path)).first():
        errors.append("That managed environment path is already registered.")

    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("main.environment_new"))

    environment = Environment(
        name=name,
        path=str(path),
        enabled=True,
        is_managed=True,
        python_interpreter=python_interpreter,
        ansible_spec=ansible_spec,
        ansible_config_path=ansible_config_path,
        proxy_credential=proxy_credential,
    )
    db.session.add(environment)
    db.session.commit()

    try:
        legacy_routes.prepare_managed_environment_build(
            environment,
            python_interpreter=python_interpreter,
            ansible_spec=ansible_spec,
            pip_requirements=pip_requirements,
            system_requirements=system_requirements,
            collections=collections,
        )
    except EnvironmentBuildError as exc:
        db.session.delete(environment)
        db.session.commit()
        flash(str(exc), "error")
        return redirect(url_for("main.environment_new"))
    record_audit_event(
        "environment.build_queued",
        result="queued",
        object_type="environment",
        object_id=environment.id,
        object_name=environment.name,
        details={
            "path": environment.path,
            "python_interpreter": environment.python_interpreter,
            "ansible_spec": environment.ansible_spec,
        },
    )
    flash("Environment build queued.", "success")
    return redirect(url_for("main.environments"))


@bp.route("/environments/<int:environment_id>/edit", methods=["GET", "POST"])
def environment_edit(environment_id):
    if not current_user_is_admin():
        abort(403)
    environment = db.get_or_404(Environment, environment_id)
    if environment.is_builtin:
        flash("Built-in environments are read-only.", "error")
        return redirect(url_for("main.environments"))

    if request.method == "POST":
        name = _clean(request.form.get("name"))
        errors = []
        ansible_config_path = _ansible_config_path_from_form(errors)
        proxy_credential = _proxy_credential_from_form(errors)
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("main.environment_edit", environment_id=environment.id))
        if not name:
            flash("Environment name is required.", "error")
            return redirect(url_for("main.environment_edit", environment_id=environment.id))
        duplicate = Environment.query.filter(Environment.name == name, Environment.id != environment.id).first()
        if duplicate:
            flash("An environment with that name already exists.", "error")
            return redirect(url_for("main.environment_edit", environment_id=environment.id))

        environment.name = name
        environment.ansible_config_path = ansible_config_path
        environment.proxy_credential = proxy_credential
        if environment.is_managed:
            limited = check_and_record_costly_operation("environment_build")
            if limited is not None:
                return limited
            try:
                legacy_routes.prepare_managed_environment_build(
                    environment,
                    python_interpreter=_clean(request.form.get("python_interpreter")),
                    ansible_spec=_clean(request.form.get("ansible_spec")) or "ansible-core",
                    pip_requirements=request.form.get("pip_requirements") or "",
                    system_requirements=request.form.get("system_requirements") or "",
                    collections=request.form.get("collections") or "",
                )
            except EnvironmentBuildError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return redirect(url_for("main.environment_edit", environment_id=environment.id))
            record_audit_event("environment.rebuild_queued", result="queued", object_type="environment", object_id=environment.id, object_name=environment.name)
            flash("Environment rebuild queued.", "success")
        else:
            environment.path = _clean(request.form.get("path"))
            db.session.commit()
            if not validate_environment(environment):
                flash(environment.validation_message or "Environment validation failed.", "error")
                return redirect(url_for("main.environment_edit", environment_id=environment.id))
            limited = check_and_record_costly_operation("environment_build")
            if limited is not None:
                return limited
            try:
                legacy_routes.prepare_registered_environment_update(
                    environment,
                    pip_requirements=request.form.get("pip_requirements") or "",
                    system_requirements=request.form.get("system_requirements") or "",
                    collections=request.form.get("collections") or "",
                )
            except EnvironmentBuildError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return redirect(url_for("main.environment_edit", environment_id=environment.id))
            record_audit_event("environment.dependencies_queued", result="queued", object_type="environment", object_id=environment.id, object_name=environment.name)
            flash("Environment updated; dependency update queued.", "success")
        return redirect(url_for("main.environments"))

    return render_template(
        "environment_edit.html",
        environment=environment,
        python_interpreters=allowed_python_interpreters(),
        proxy_credentials=_environment_proxy_credentials(),
    )


@bp.post("/environments/<int:environment_id>/validate")
def environment_validate(environment_id):
    if not current_user_is_admin():
        abort(403)
    environment = db.get_or_404(Environment, environment_id)
    passed = validate_environment(environment)
    record_audit_event("environment.validated", result="success" if passed else "failed", object_type="environment", object_id=environment.id, object_name=environment.name, details={"message": environment.validation_message})
    flash("Environment validation passed." if passed else environment.validation_message, "success" if passed else "error")
    return redirect(url_for("main.environments"))


@bp.route("/environments/<int:environment_id>/sync", methods=["GET", "POST"])
def environment_sync(environment_id):
    if not current_user_is_admin():
        abort(403)

    environment = db.get_or_404(Environment, environment_id)
    remote_runners = (
        Runner.query
        .filter(
            Runner.is_local.is_(False),
            Runner.enabled.is_(True),
            Runner.runner_uuid.isnot(None),
            Runner.api_secret_digest != "",
        )
        .order_by(Runner.name.asc())
        .all()
    )

    if request.method == "POST":
        selected_ids = {
            int(value)
            for value in request.form.getlist("runner_ids")
            if str(value).isdigit()
        }
        selected = [runner for runner in remote_runners if runner.id in selected_ids]
        if not selected:
            flash("Select at least one registered remote runner.", "error")
            return redirect(url_for("main.environment_sync", environment_id=environment.id))

        queued = []
        errors = []
        for runner in selected:
            try:
                queue_environment_sync(environment, runner)
                queued.append(runner)
            except RunnerEnvironmentSyncError as exc:
                errors.append('{}: {}'.format(runner.name, exc))

        if queued:
            db.session.commit()
            record_audit_event(
                "environment.runner_sync.queued",
                result="queued",
                object_type="environment",
                object_id=environment.id,
                object_name=environment.name,
                details={"runner_ids": [runner.id for runner in queued]},
            )
            flash(
                'Environment synchronization queued for {} runner(s).'.format(len(queued)),
                "success",
            )
        else:
            db.session.rollback()
        for error in errors:
            flash(error, "error")
        return redirect(url_for("main.environment_sync", environment_id=environment.id))

    return render_template(
        "environment_sync.html",
        environment=environment,
        runners=environment_sync_rows(environment, remote_runners),
        syncable=is_syncable_environment(environment),
    )


@bp.post("/environments/<int:environment_id>/default")
def environment_make_default(environment_id):
    if not current_user_is_admin():
        abort(403)
    environment = db.get_or_404(Environment, environment_id)
    if environment.name == APPLICATION_ENVIRONMENT_NAME:
        flash("The Journeyman application environment is not an execution environment.", "error")
        return redirect(url_for("main.environments"))
    if not environment.enabled or environment.validation_status != "passed":
        flash("Only an enabled, validated environment can be the default.", "error")
        return redirect(url_for("main.environments"))
    Environment.query.update({Environment.is_default: False})
    environment.is_default = True
    db.session.commit()
    record_audit_event("environment.default_changed", object_type="environment", object_id=environment.id, object_name=environment.name)
    flash("Default environment updated.", "success")
    return redirect(url_for("main.environments"))


@bp.post("/environments/<int:environment_id>/toggle")
def environment_toggle(environment_id):
    if not current_user_is_admin():
        abort(403)
    environment = db.get_or_404(Environment, environment_id)
    if environment.is_builtin:
        flash("Built-in environments cannot be disabled.", "error")
    elif environment.is_default:
        flash("Choose another default before disabling this environment.", "error")
    else:
        environment.enabled = not environment.enabled
        db.session.commit()
        record_audit_event("environment.enabled_changed", object_type="environment", object_id=environment.id, object_name=environment.name, details={"enabled": environment.enabled})
        flash("Environment updated.", "success")
    return redirect(url_for("main.environments"))


@bp.post("/environments/<int:environment_id>/delete")
def environment_delete(environment_id):
    if not current_user_is_admin():
        abort(403)
    environment = db.get_or_404(Environment, environment_id)
    if environment.is_builtin or environment.is_default:
        flash("The built-in or default environment cannot be deleted.", "error")
    elif environment.project_steps:
        flash("This environment is assigned to one or more project steps.", "error")
    else:
        name = environment.name
        was_managed = environment.is_managed
        try:
            if was_managed:
                delete_managed_environment_files(environment)
        except EnvironmentBuildError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.environments"))
        db.session.delete(environment)
        db.session.commit()
        record_audit_event(
            "environment.deleted",
            object_type="environment",
            object_id=environment_id,
            object_name=name,
            details={"managed_files_removed": was_managed},
        )
        flash(
            "Managed environment and its files were deleted." if was_managed else "Environment registration deleted. The virtualenv itself was not removed.",
            "success",
        )
    return redirect(url_for("main.environments"))
