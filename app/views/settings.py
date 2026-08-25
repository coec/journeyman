"""System and environment-build settings routes."""

from flask import abort, flash, redirect, render_template, request, url_for

from app import db
from app.auth import current_user_is_admin, current_username
from app.services.user_preferences import get_or_create_user_preferences
from app.services.name_ordering import reserved_name_ordering
from app.models.system_setting import APPLY_STATUS_APPLIED, APPLY_STATUS_FAILED, utcnow
from app.routes import bp
from app.services.audit import record_audit_event
from app.services.data_retention import (
    DataRetentionValidationError,
    retention_settings_form_data,
    retention_settings_to_form_data,
    update_retention_settings,
    validate_retention_settings,
)
from app.services.environment_build_settings import (
    EnvironmentBuildSettingsError,
    form_data as environment_build_form_data,
    get_or_create_environment_build_settings,
    settings_to_form_data as environment_build_settings_to_form_data,
    test_proxy as test_environment_build_proxy,
    update as update_environment_build_settings,
    validate as validate_environment_build_settings,
)
from app.services.system_settings import (
    SystemSettingsValidationError,
    get_or_create_system_settings,
    settings_to_form_data,
    system_settings_form_data,
    update_system_settings,
    validate_system_settings,
)
from app.services.system_settings_apply import (
    SystemSettingsApplyError,
    apply_nginx_settings,
)


@bp.route("/settings/environment-builds", methods=["GET", "POST"])
def environment_build_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_environment_build_settings()
    errors = []

    if request.method == "POST":
        values = environment_build_form_data(request.form)
        try:
            values = validate_environment_build_settings(values)
            update_environment_build_settings(
                settings,
                values,
                username=current_username(),
            )
        except EnvironmentBuildSettingsError as exc:
            errors = [str(exc)]
            return (
                render_template(
                    "environment_build_settings.html",
                    settings=settings,
                    form_data=values,
                    errors=errors,
                ),
                400,
            )

        record_audit_event(
            "environment_build_settings.updated",
            object_type="environment_build_settings",
            object_id=str(settings.id),
            object_name="Environment build proxy",
            details={
                "proxy_enabled": bool(settings.proxy_enabled),
                "proxy_url": settings.proxy_url,
                "proxy_username_configured": bool(settings.proxy_username),
                "proxy_password_configured": settings.has_proxy_password(),
                "no_proxy": settings.no_proxy,
            },
        )
        flash("Environment build settings saved.", "success")
        return redirect(url_for("main.environment_build_settings"))

    return render_template(
        "environment_build_settings.html",
        settings=settings,
        form_data=environment_build_settings_to_form_data(settings),
        errors=errors,
    )


@bp.post("/settings/environment-builds/test")
def environment_build_proxy_test():
    if not current_user_is_admin():
        abort(403)

    try:
        message = test_environment_build_proxy()
    except EnvironmentBuildSettingsError as exc:
        record_audit_event(
            "environment_build_proxy.test",
            result="failure",
            object_type="environment_build_settings",
            object_name="Environment build proxy",
            details={"message": str(exc)},
        )
        flash(str(exc), "error")
    else:
        record_audit_event(
            "environment_build_proxy.test",
            result="success",
            object_type="environment_build_settings",
            object_name="Environment build proxy",
            details={"message": message},
        )
        flash(f"Environment build proxy test passed. {message}", "success")

    return redirect(url_for("main.environment_build_settings"))


@bp.route("/settings/data-retention", methods=["GET", "POST"])
def data_retention_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_system_settings()
    errors = []

    if request.method == "POST":
        values = retention_settings_form_data(request.form)
        try:
            validated = validate_retention_settings(values)
        except DataRetentionValidationError as exc:
            errors = list(exc.errors)
            return (
                render_template(
                    "data_retention_settings.html",
                    settings=settings,
                    form_data=values,
                    errors=errors,
                ),
                400,
            )

        update_retention_settings(
            settings,
            validated,
            updated_by=current_username(),
        )
        record_audit_event(
            "data_retention_settings.updated",
            object_type="system_setting",
            object_id=str(settings.id),
            object_name="Data retention",
            details={
                "job_retention_days": settings.job_retention_days,
                "reaction_retention_days": settings.reaction_retention_days,
            },
        )
        flash("Data retention settings saved.", "success")
        return redirect(url_for("main.data_retention_settings"))

    return render_template(
        "data_retention_settings.html",
        settings=settings,
        form_data=retention_settings_to_form_data(settings),
        errors=errors,
    )


@bp.route("/settings", methods=["GET", "POST"])
def system_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_system_settings()
    errors = []

    if request.method == "POST":
        form_data = system_settings_form_data(request.form)

        try:
            validated_values = validate_system_settings(form_data)
        except SystemSettingsValidationError as exc:
            errors = list(exc.errors)

            return (
                render_template(
                    "system_settings.html",
                    settings=settings,
                    form_data=form_data,
                    errors=errors,
                ),
                400,
            )

        update_system_settings(
            settings,
            validated_values,
            updated_by=current_username(),
        )

        flash(
            "System settings saved. Nginx has not yet been reconfigured.",
            "success",
        )

        return redirect(url_for("main.system_settings"))

    return render_template(
        "system_settings.html",
        settings=settings,
        form_data=settings_to_form_data(settings),
        errors=errors,
    )


@bp.post("/settings/apply")
def apply_system_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_system_settings()

    try:
        result = apply_nginx_settings(settings)
    except SystemSettingsApplyError as exc:
        settings.apply_status = APPLY_STATUS_FAILED
        settings.apply_message = str(exc)
        db.session.commit()

        flash(
            "Nginx settings were not applied: {}".format(exc),
            "error",
        )
        return redirect(url_for("main.system_settings"))

    settings.apply_status = APPLY_STATUS_APPLIED
    settings.apply_message = result["message"]
    settings.applied_config_sha256 = result["configuration_sha256"]
    settings.last_applied_at = utcnow()
    db.session.commit()

    flash("Nginx configuration applied successfully.", "success")
    return redirect(url_for("main.system_settings"))

@bp.route("/preferences", methods=["GET", "POST"])
def user_preferences():
    preferences = get_or_create_user_preferences(current_username())
    if request.method == "POST":
        preferences.hide_disabled_projects = request.form.get("hide_disabled_projects") == "1"
        preferences.hide_disabled_packages = request.form.get("hide_disabled_packages") == "1"
        rows_per_page = request.form.get("rows_per_page", type=int)
        preferences.rows_per_page = rows_per_page if rows_per_page in (25, 50, 100, 200) else 50
        db.session.commit()
        flash("Preferences saved.", "success")
        return redirect(url_for("main.user_preferences"))
    return render_template("user_preferences.html", preferences=preferences)


@bp.route("/settings/release-testing", methods=["GET", "POST"])
def release_testing_settings():
    if not current_user_is_admin():
        abort(403)

    from app.credential_types import CREDENTIAL_TYPE_MACHINE
    from app.models import Credential, Inventory, RunnerCrew
    from app.services.builtin_automation import ensure_builtin_release_validation
    from app.services.release_testing import (
        ReleaseTestSettingsError,
        form_data as release_test_form_data,
        get_or_create_release_test_settings,
        is_configured,
        latest_validation_result,
        settings_to_form_data as release_test_settings_to_form_data,
        update as update_release_test_settings,
        validate as validate_release_test_settings,
    )

    settings = get_or_create_release_test_settings()
    errors = []

    if request.method == "POST":
        values = release_test_form_data(request.form)
        try:
            values = validate_release_test_settings(values)
            update_release_test_settings(settings, values, current_username())
            seeded = ensure_builtin_release_validation(settings)
        except ReleaseTestSettingsError as exc:
            errors = list(exc.errors)
            form_values = values
        except RuntimeError as exc:
            errors = [str(exc)]
            form_values = values
        else:
            record_audit_event(
                "release_testing_settings.updated",
                object_type="release_test_settings",
                object_id=str(settings.id),
                object_name="Release Testing",
                details={
                    "inventory_id": settings.inventory_id,
                    "credential_id": settings.credential_id,
                    "runner_crew_id": settings.runner_crew_id,
                    "host_pattern": settings.host_pattern,
                    "alternate_become_users": settings.become_users(),
                    "package_id": seeded["package"].id if seeded else None,
                },
            )
            flash("Release testing settings saved.", "success")
            return redirect(url_for("main.release_testing_settings"))
    else:
        form_values = release_test_settings_to_form_data(settings)

    package = None
    failure_package = None
    validation_result = None
    failure_validation_result = None
    if is_configured(settings):
        try:
            seeded = ensure_builtin_release_validation(settings)
        except RuntimeError as exc:
            errors.append(str(exc))
        else:
            package = seeded["package"] if seeded else None
            failure_package = seeded["failure_package"] if seeded else None
            validation_result = latest_validation_result(seeded["project"])
            failure_validation_result = latest_validation_result(
                seeded["failure_project"], expected_failure=True
            )

    inventories = (
        Inventory.query.filter_by(enabled=True)
        .order_by(*reserved_name_ordering(Inventory.name))
        .all()
    )
    credentials = (
        Credential.query.filter_by(credential_type=CREDENTIAL_TYPE_MACHINE)
        .order_by(*reserved_name_ordering(Credential.name))
        .all()
    )
    runner_crews = (
        RunnerCrew.query.filter_by(enabled=True)
        .order_by(*reserved_name_ordering(RunnerCrew.name))
        .all()
    )

    return render_template(
        "release_testing_settings.html",
        settings=settings,
        form_data=form_values,
        errors=errors,
        inventories=inventories,
        credentials=credentials,
        runner_crews=runner_crews,
        validation_package=package,
        failure_validation_package=failure_package,
        validation_result=validation_result,
        failure_validation_result=failure_validation_result,
    )
