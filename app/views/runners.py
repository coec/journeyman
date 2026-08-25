"""Runner administration and runner-facing API routes."""

from datetime import datetime, timezone
import json
import secrets
import time

from flask import (
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
    url_for,
)

from app import csrf, db
from app.auth import current_user_is_admin
from app.models import Job, JobStepExecutionSlice, ProjectPackage, Runner, RunnerCrew
from app.routes import bp
from app.services.audit import record_audit_event
from app.services.builtin_automation import (
    REMOTE_RUNNER_BUILTIN_KEY,
    ensure_builtin_admin_automation,
)
from app.services.runners import (
    authenticate_runner,
    delete_runner,
    issue_registration_token,
    RunnerRemovalError,
    CURRENT_REMOTE_RUNNER_VERSION,
    runner_update_available,
    register_runner,
    unregister_runner,
    runner_health,
)
from app.services.runner_dispatch import claim_next_remote_job, job_assignment_manifest
from app.services.runner_capabilities import (
    runner_capability_rows,
    runner_capability_update_required,
    set_reported_capabilities,
)
from app.services.runner_environments import (
    runner_environment_rows,
    set_reported_runner_environments,
)
from app.services.runner_runtime_dependencies import (
    runner_runtime_dependency_names_for_reporting,
    runner_runtime_dependency_state,
    runner_runtime_dependency_update_required,
    set_reported_runner_runtime_dependencies,
)
from app.services.runner_environment_sync import (
    RunnerEnvironmentSyncError,
    claim_next_environment_sync,
    complete_environment_sync,
    environment_sync_manifest,
)
from app.services.runner_slice_dispatch import (
    claim_next_remote_slice,
    slice_assignment_manifest,
)
from app.services.runner_artifacts import (
    RunnerArtifactError,
    cleanup_job_repository_artifacts,
    prepare_job_repository_artifacts,
    repository_artifact_path,
)
from app.services.runner_job_lifecycle import (
    complete_remote_job,
    remote_job_control,
    start_remote_job,
)
from app.services.runner_slice_lifecycle import (
    assignment_matches as slice_assignment_matches,
    complete_remote_slice,
    remote_slice_control,
    start_remote_slice,
    update_remote_slice_output,
)
from app.services.runner_execution_data import (
    RunnerExecutionDataError,
    encrypt_execution_data,
)
from app.services.job_inventory_refresh import (
    JobInventoryRefreshError,
    refresh_job_inventories_after_step,
)




def _json_object_payload():
    """Return a JSON object payload or a safe API error response."""

    if not request.is_json:
        return None, (
            jsonify({"error": "Content-Type must be application/json."}),
            415,
        )

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (
            jsonify({"error": "Request body must be a JSON object."}),
            400,
        )

    return payload, None

def _runner_structural_fingerprint(rows):
    """Return runner state that requires rebuilding rendered table rows."""

    return tuple(
        (
            item["runner"].id,
            item["runner"].name,
            item["runner"].runner_uuid,
            item["runner"].is_local,
            item["runner"].enabled,
            item["runner"].site,
            item["runner"].hostname,
            item["runner"].version,
            tuple(sorted(item["runner"].capabilities())),
            tuple(crew.name for crew in item["runner"].crews),
            tuple(
                (
                    capability["key"],
                    capability["state"],
                    capability["message"],
                )
                for capability in item["managed_capabilities"]
            ),
            tuple(
                (
                    row["environment"].id,
                    row["state"],
                    row["reported_revision"],
                    row["local_path"],
                    row["message"],
                )
                for row in item["execution_environments"]
            ),
            item["update_available"],
            item["capability_update_required"],
            item["runtime_dependencies"]["state"],
            item["runtime_dependencies"]["audit_status"],
            item["runtime_dependencies"]["audit_message"],
            tuple(
                (row["name"], row["reported"], row["expected"])
                for row in item["runtime_dependencies"]["drift"]
            ),
        )
        for item in rows
    )


def _runner_live_payload(rows):
    """Return lightweight values that can be patched into existing rows."""

    return {
        "runners": [
            {
                "id": item["runner"].id,
                "health": item["health"],
                "last_heartbeat_at": (
                    item["runner"].last_heartbeat_at.isoformat()
                    if item["runner"].last_heartbeat_at
                    else None
                ),
                "running_steps": item["runner"].running_steps,
                "max_concurrent_steps": item["runner"].max_concurrent_steps,
                "load_average_1m": item["runner"].load_average_1m,
                "load_average_5m": item["runner"].load_average_5m,
                "cpu_count": item["runner"].cpu_count,
            }
            for item in rows
        ]
    }


def _runner_rows():
    rows = Runner.query.order_by(Runner.name.asc()).all()
    return [
        {
            "runner": runner,
            "health": runner_health(runner),
            "update_available": runner_update_available(runner),
            "managed_capabilities": runner_capability_rows(runner),
            "capability_update_required": runner_capability_update_required(runner),
            "execution_environments": runner_environment_rows(runner),
            "runtime_dependencies": runner_runtime_dependency_state(runner),
            "runtime_dependency_update_required": runner_runtime_dependency_update_required(runner),
        }
        for runner in rows
    ]


@bp.get("/runners")
def runners():
    if not current_user_is_admin():
        abort(403)

    package = ProjectPackage.query.filter_by(
        builtin_key=REMOTE_RUNNER_BUILTIN_KEY
    ).one_or_none()
    if package is None or request.headers.get("X-Journeyman-Live-Refresh") != "1":
        package = ensure_builtin_admin_automation()["package"]

    return render_template(
        "runners.html",
        runners=_runner_rows(),
        manage_runner_package=package,
        current_remote_runner_version=CURRENT_REMOTE_RUNNER_VERSION,
    )


@bp.get("/runners/events")
def runner_events():
    """Stream runner heartbeat, capacity, and health changes to the UI."""

    if not current_user_is_admin():
        abort(403)

    def generate():
        last_structural_fingerprint = None
        last_live_payload = None
        heartbeat_counter = 0

        try:
            while True:
                # End the previous read transaction before querying again.
                # SQLite otherwise keeps the streaming request on one snapshot,
                # so heartbeats committed by the runner process are never seen.
                db.session.remove()
                rows = _runner_rows()
                structural_fingerprint = _runner_structural_fingerprint(rows)
                live_payload = _runner_live_payload(rows)

                if last_structural_fingerprint is None:
                    # The page was rendered immediately before opening this
                    # stream, so the first event only needs to synchronize live
                    # values. Avoid an immediate full DOM replacement.
                    yield "event: runner-update\ndata: {}\n\n".format(
                        json.dumps(live_payload)
                    )
                    last_structural_fingerprint = structural_fingerprint
                    last_live_payload = live_payload
                    heartbeat_counter = 0
                elif structural_fingerprint != last_structural_fingerprint:
                    yield "event: runner-refresh\ndata: {}\n\n".format(
                        json.dumps({"runner_count": len(rows)})
                    )
                    last_structural_fingerprint = structural_fingerprint
                    last_live_payload = live_payload
                    heartbeat_counter = 0
                elif live_payload != last_live_payload:
                    yield "event: runner-update\ndata: {}\n\n".format(
                        json.dumps(live_payload)
                    )
                    last_live_payload = live_payload
                    heartbeat_counter = 0
                else:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 15:
                        yield ": heartbeat\n\n"
                        heartbeat_counter = 0

                time.sleep(2)
        finally:
            db.session.remove()

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/runner-crews")
def runner_crews():
    if not current_user_is_admin():
        abort(403)

    crews = RunnerCrew.query.order_by(RunnerCrew.name.asc()).all()
    remote_runners = (
        Runner.query
        .filter(Runner.is_local.is_(False))
        .order_by(Runner.name.asc())
        .all()
    )
    return render_template(
        "runner_crews.html",
        crews=crews,
        remote_runners=remote_runners,
    )


@bp.post("/runner-crews/new")
def runner_crew_create():
    if not current_user_is_admin():
        abort(403)

    name = str(request.form.get("name") or "").strip()
    description = str(request.form.get("description") or "").strip()
    if not name:
        flash("Runner Crew name is required.", "error")
        return redirect(url_for("main.runner_crews"))
    if RunnerCrew.query.filter_by(name=name).first() is not None:
        flash('Runner Crew "{}" already exists.'.format(name), "error")
        return redirect(url_for("main.runner_crews"))

    crew = RunnerCrew(name=name, description=description, enabled=True)
    db.session.add(crew)
    db.session.flush()
    member_ids = {
        int(value) for value in request.form.getlist("runner_ids")
        if str(value).isdigit()
    }
    crew.runners = (
        Runner.query
        .filter(Runner.id.in_(member_ids), Runner.is_local.is_(False))
        .order_by(Runner.name.asc())
        .all()
        if member_ids else []
    )
    db.session.commit()
    record_audit_event(
        "runner_crew.create",
        object_type="runner_crew",
        object_id=crew.id,
        object_name=crew.name,
        details={"runner_ids": sorted(member_ids)},
    )
    flash('Runner Crew "{}" created.'.format(crew.name), "success")
    return redirect(url_for("main.runner_crews"))


@bp.post("/runner-crews/<int:crew_id>/update")
def runner_crew_update(crew_id):
    if not current_user_is_admin():
        abort(403)
    crew = db.get_or_404(RunnerCrew, crew_id)
    name = str(request.form.get("name") or "").strip()
    if not name:
        flash("Runner Crew name is required.", "error")
        return redirect(url_for("main.runner_crews"))
    duplicate = RunnerCrew.query.filter(RunnerCrew.name == name, RunnerCrew.id != crew.id).first()
    if duplicate is not None:
        flash('Runner Crew "{}" already exists.'.format(name), "error")
        return redirect(url_for("main.runner_crews"))

    member_ids = {
        int(value) for value in request.form.getlist("runner_ids")
        if str(value).isdigit()
    }
    crew.name = name
    crew.description = str(request.form.get("description") or "").strip()
    crew.enabled = request.form.get("enabled") == "on"
    crew.runners = (
        Runner.query
        .filter(Runner.id.in_(member_ids), Runner.is_local.is_(False))
        .order_by(Runner.name.asc())
        .all()
        if member_ids else []
    )
    db.session.commit()
    record_audit_event(
        "runner_crew.update",
        object_type="runner_crew",
        object_id=crew.id,
        object_name=crew.name,
        details={"runner_ids": sorted(member_ids), "enabled": crew.enabled},
    )
    flash('Runner Crew "{}" updated.'.format(crew.name), "success")
    return redirect(url_for("main.runner_crews"))


@bp.post("/runner-crews/<int:crew_id>/delete")
def runner_crew_delete(crew_id):
    if not current_user_is_admin():
        abort(403)
    crew = db.get_or_404(RunnerCrew, crew_id)
    project_count = len(crew.projects)
    job_count = Job.query.filter_by(default_runner_crew_id=crew.id).count()
    if project_count or job_count:
        references = []
        if project_count:
            references.append("{} Project{}".format(project_count, "" if project_count == 1 else "s"))
        if job_count:
            references.append("{} historical Job{}".format(job_count, "" if job_count == 1 else "s"))
        flash(
            'Runner Crew "{}" cannot be deleted while referenced by {}.'.format(
                crew.name, " and ".join(references)
            ),
            "error",
        )
        return redirect(url_for("main.runner_crews"))
    crew_name = crew.name
    crew_id_value = crew.id
    db.session.delete(crew)
    db.session.commit()
    record_audit_event(
        "runner_crew.delete",
        object_type="runner_crew",
        object_id=crew_id_value,
        object_name=crew_name,
    )
    flash('Runner Crew "{}" deleted.'.format(crew_name), "success")
    return redirect(url_for("main.runner_crews"))


@bp.post("/runners/new")
def runner_create():
    if not current_user_is_admin():
        abort(403)

    name = str(request.form.get("name") or "").strip()
    site = str(request.form.get("site") or "").strip()
    capabilities = [
        str(item).strip().lower()
        for item in request.form.getlist("capabilities")
        if str(item).strip()
    ]
    allowed_capabilities = {"ansible", "shell"}
    try:
        maximum = int(request.form.get("max_concurrent_steps") or 1)
    except ValueError:
        maximum = 0

    if not name:
        flash("Runner name is required.", "error")
        return redirect(url_for("main.runners"))
    if not capabilities:
        flash("Select at least one runner capability.", "error")
        return redirect(url_for("main.runners"))
    if any(item not in allowed_capabilities for item in capabilities):
        flash("One or more runner capabilities are invalid.", "error")
        return redirect(url_for("main.runners"))
    capabilities = sorted(set(capabilities))
    if maximum < 1 or maximum > 100:
        flash("Maximum concurrent steps must be between 1 and 100.", "error")
        return redirect(url_for("main.runners"))
    if Runner.query.filter_by(name=name).first() is not None:
        flash('Runner "{}" already exists.'.format(name), "error")
        return redirect(url_for("main.runners"))

    runner = Runner(
        name=name,
        site=site,
        max_concurrent_steps=maximum,
        enabled=True,
    )
    runner.set_capabilities(capabilities)
    token = issue_registration_token(runner)
    db.session.add(runner)
    db.session.commit()
    record_audit_event(
        "runner.create",
        object_type="runner",
        object_id=runner.id,
        object_name=runner.name,
        details={"site": runner.site},
    )
    return render_template(
        "runners.html",
        runners=_runner_rows(),
        manage_runner_package=ensure_builtin_admin_automation()["package"],
        registration_token=token,
        registration_runner=runner,
    )


@bp.post("/runners/<int:runner_id>/toggle")
def runner_toggle(runner_id):
    if not current_user_is_admin():
        abort(403)
    runner = db.get_or_404(Runner, runner_id)
    if runner.is_local:
        abort(400)
    runner.enabled = not runner.enabled
    db.session.commit()
    record_audit_event(
        "runner.enable" if runner.enabled else "runner.disable",
        object_type="runner",
        object_id=runner.id,
        object_name=runner.name,
    )
    return redirect(url_for("main.runners"))


@bp.post("/runners/<int:runner_id>/unregister")
def runner_unregister(runner_id):
    if not current_user_is_admin():
        abort(403)
    runner = db.get_or_404(Runner, runner_id)
    runner_name = runner.name
    runner_object_id = runner.id
    runner_uuid = runner.runner_uuid
    try:
        unregister_runner(runner)
    except RunnerRemovalError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.runners"))
    record_audit_event(
        "runner.unregister",
        object_type="runner",
        object_id=runner_object_id,
        object_name=runner_name,
        details={"runner_uuid": runner_uuid},
    )
    flash(
        'Runner "{}" unregistered. Its API credential has been revoked.'.format(
            runner_name
        ),
        "success",
    )
    return redirect(url_for("main.runners"))


@bp.post("/runners/<int:runner_id>/delete")
def runner_delete(runner_id):
    if not current_user_is_admin():
        abort(403)
    runner = db.get_or_404(Runner, runner_id)
    runner_name = runner.name
    runner_object_id = runner.id
    try:
        delete_runner(runner)
    except RunnerRemovalError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.runners"))
    record_audit_event(
        "runner.delete",
        object_type="runner",
        object_id=runner_object_id,
        object_name=runner_name,
    )
    flash('Runner "{}" deleted.'.format(runner_name), "success")
    return redirect(url_for("main.runners"))


@bp.post("/runners/<int:runner_id>/new-registration-token")
def runner_new_registration_token(runner_id):
    if not current_user_is_admin():
        abort(403)
    runner = db.get_or_404(Runner, runner_id)
    if runner.is_local:
        abort(400)
    if runner.is_registered:
        flash(
            "A registered runner cannot receive a new registration token.",
            "error",
        )
        return redirect(url_for("main.runners"))
    token = issue_registration_token(runner)
    db.session.commit()
    record_audit_event(
        "runner.registration_token.rotate",
        object_type="runner",
        object_id=runner.id,
        object_name=runner.name,
    )
    return render_template(
        "runners.html",
        runners=_runner_rows(),
        registration_token=token,
        registration_runner=runner,
    )


@bp.post("/api/runners/register")
@csrf.exempt
def runner_register_api():
    payload, error = _json_object_payload()
    if error is not None:
        return error
    runner, secret = register_runner(
        str(payload.get("token") or ""),
        hostname=payload.get("hostname"),
        version=payload.get("version"),
    )
    if runner is None:
        return jsonify(
            {"error": "Invalid, expired, or already-used registration token."}
        ), 403
    record_audit_event(
        "runner.register",
        object_type="runner",
        object_id=runner.id,
        object_name=runner.name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
    )
    return jsonify(
        {
            "runner_uuid": runner.runner_uuid,
            "runner_secret": secret,
            "name": runner.name,
            "site": runner.site,
            "heartbeat_url": url_for(
                "main.runner_heartbeat_api",
                _external=True,
            ),
        }
    )


@bp.post("/api/runners/unregister")
@csrf.exempt
def runner_unregister_api():
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403

    payload, error = _json_object_payload()
    if error is not None:
        return error
    delete_requested = payload.get("delete") is True
    runner_id = runner.id
    runner_name = runner.name
    runner_uuid = runner.runner_uuid
    try:
        if delete_requested:
            delete_runner(runner)
            action = "runner.delete.self"
            status = "deleted"
        else:
            unregister_runner(runner)
            action = "runner.unregister"
            status = "unregistered"
    except RunnerRemovalError as exc:
        return jsonify({"error": str(exc)}), 409

    record_audit_event(
        action,
        object_type="runner",
        object_id=runner_id,
        object_name=runner_name,
        actor_username="runner:{}".format(runner_uuid),
        authenticated_via="runner-secret",
    )
    return jsonify({"status": status, "name": runner_name})


@bp.post("/api/runners/heartbeat")
@csrf.exempt
def runner_heartbeat_api():
    runner_uuid = str(request.headers.get("X-Journeyman-Runner-ID") or "")
    authorization = str(request.headers.get("Authorization") or "")
    secret = authorization[7:] if authorization.startswith("Bearer ") else ""
    runner = authenticate_runner(runner_uuid, secret)
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403

    payload, error = _json_object_payload()
    if error is not None:
        return error
    runner.hostname = str(payload.get("hostname") or runner.hostname or "")[:255]
    runner.version = str(payload.get("version") or runner.version or "")[:120]
    runner.status_message = str(payload.get("status_message") or "")[:2000]
    if "capabilities" in payload:
        if not isinstance(payload.get("capabilities"), list):
            return jsonify({"error": "capabilities must be a list."}), 400
        runner.set_capabilities(payload.get("capabilities"))
    if "managed_capabilities" in payload:
        if not isinstance(payload.get("managed_capabilities"), dict):
            return jsonify({"error": "managed_capabilities must be an object."}), 400
        set_reported_capabilities(runner, payload.get("managed_capabilities"))
    if "environments" in payload:
        try:
            set_reported_runner_environments(runner, payload.get("environments"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "runtime_dependencies" in payload:
        try:
            set_reported_runner_runtime_dependencies(
                runner, payload.get("runtime_dependencies")
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    try:
        runner.running_steps = max(0, int(payload.get("running_steps") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "running_steps must be an integer."}), 400

    for key, attribute in (
        ("load_average_1m", "load_average_1m"),
        ("load_average_5m", "load_average_5m"),
    ):
        if key in payload and payload.get(key) is not None:
            try:
                setattr(runner, attribute, max(0.0, float(payload.get(key))))
            except (TypeError, ValueError):
                return jsonify({"error": "{} must be numeric.".format(key)}), 400

    if "cpu_count" in payload and payload.get("cpu_count") is not None:
        try:
            runner.cpu_count = max(1, int(payload.get("cpu_count")))
        except (TypeError, ValueError):
            return jsonify({"error": "cpu_count must be an integer."}), 400

    free_bytes = payload.get("free_workspace_bytes")
    if free_bytes is not None:
        try:
            runner.free_workspace_bytes = max(0, int(free_bytes))
        except (TypeError, ValueError):
            return jsonify(
                {"error": "free_workspace_bytes must be an integer."}
            ), 400
    runner.last_heartbeat_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({
        "status": "accepted",
        "enabled": runner.enabled,
        "max_concurrent_steps": runner.max_concurrent_steps,
        "runtime_dependency_names": runner_runtime_dependency_names_for_reporting(),
    })


@bp.post("/api/runners/environments/claim")
@csrf.exempt
def runner_environment_sync_claim_api():
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    if not runner.enabled:
        return ("", 204)

    sync = claim_next_environment_sync(runner)
    if sync is None:
        return ("", 204)
    try:
        manifest = environment_sync_manifest(sync)
    except Exception:
        sync.status = "failed"
        sync.message = "Unable to prepare Environment synchronization payload."
        sync.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({"error": "environment_sync_preparation_failed"}), 500

    manifest["complete_url"] = url_for(
        "main.runner_environment_sync_complete_api",
        sync_id=sync.id,
    )
    record_audit_event(
        "runner.environment_sync.claim",
        result="building",
        object_type="runner_environment_sync",
        object_id=sync.id,
        object_name=sync.environment.name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
        details={"runner_id": runner.id, "environment_id": sync.environment_id},
    )
    response = jsonify(manifest)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.post("/api/runners/environments/<int:sync_id>/complete")
@csrf.exempt
def runner_environment_sync_complete_api(sync_id):
    from app.models import RunnerEnvironmentSync

    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    sync = db.session.get(RunnerEnvironmentSync, sync_id)
    if sync is None:
        return jsonify({"error": "Environment synchronization was not found."}), 404
    payload, error = _json_object_payload()
    if error is not None:
        return error
    try:
        complete_environment_sync(sync, runner, payload)
    except RunnerEnvironmentSyncError as exc:
        return jsonify({"error": str(exc)}), 409

    record_audit_event(
        "runner.environment_sync.complete",
        result=sync.status,
        object_type="runner_environment_sync",
        object_id=sync.id,
        object_name=sync.environment.name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
        details={
            "runner_id": runner.id,
            "environment_id": sync.environment_id,
            "message": sync.message,
        },
    )
    return jsonify({"status": sync.status})


@bp.post("/api/runners/jobs/claim")
@csrf.exempt
def runner_job_claim_api():
    runner_uuid = str(request.headers.get("X-Journeyman-Runner-ID") or "")
    authorization = str(request.headers.get("Authorization") or "")
    secret = authorization[7:] if authorization.startswith("Bearer ") else ""
    runner = authenticate_runner(runner_uuid, secret)
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403

    execution_slice, token = claim_next_remote_slice(runner)
    if execution_slice is not None:
        job = execution_slice.step.job
        try:
            repository_artifacts = prepare_job_repository_artifacts(job)
        except RunnerArtifactError as exc:
            execution_slice.status = "failed"
            execution_slice.exit_code = 1
            execution_slice.finished_at = datetime.now(timezone.utc)
            execution_slice.message = "Unable to prepare remote repository artefacts: {}".format(exc)
            execution_slice.dispatch_token = ""
            db.session.commit()
            return jsonify({"error": "repository_artifact_preparation_failed"}), 500

        for artifact in repository_artifacts:
            artifact["download_url"] = url_for(
                "main.runner_slice_repository_artifact_api",
                slice_id=execution_slice.id,
                snapshot_id=artifact["snapshot_id"],
                _external=True,
            )
        execution_data_url = url_for(
            "main.runner_slice_execution_data_api",
            slice_id=execution_slice.id,
            _external=True,
        )
        record_audit_event(
            "runner.slice.assign",
            result="queued",
            object_type="job_step_execution_slice",
            object_id=execution_slice.id,
            object_name="Job #{} step {} slice {}".format(
                job.id, execution_slice.step.position, execution_slice.position
            ),
            actor_username="runner:{}".format(runner.runner_uuid),
            authenticated_via="runner-token",
            details={
                "job_id": job.id,
                "step_position": execution_slice.step.position,
                "runner_id": runner.id,
                "runner_name": runner.name,
                "hosts": execution_slice.get_hosts(),
            },
        )
        return jsonify(
            slice_assignment_manifest(
                execution_slice, token, repository_artifacts, execution_data_url
            )
        )

    job, token = claim_next_remote_job(runner)
    if job is None:
        return ("", 204)
    try:
        repository_artifacts = prepare_job_repository_artifacts(job)
    except RunnerArtifactError as exc:
        job.status = "failed"
        job.exit_code = 1
        job.finished_at = datetime.now(timezone.utc)
        job.message = "Unable to prepare remote repository artefacts: {}".format(exc)
        job.dispatch_token = ""
        db.session.commit()
        record_audit_event(
            "runner.job.artifact.prepare",
            result="failed",
            object_type="job",
            object_id=job.id,
            object_name=job.project_name,
            actor_username="runner:{}".format(runner.runner_uuid),
            authenticated_via="runner-token",
            details={"error": str(exc)},
        )
        return jsonify({"error": "repository_artifact_preparation_failed"}), 500

    for artifact in repository_artifacts:
        artifact["download_url"] = url_for(
            "main.runner_job_repository_artifact_api",
            job_id=job.id,
            snapshot_id=artifact["snapshot_id"],
            _external=True,
        )

    record_audit_event(
        "runner.job.assign",
        result="queued",
        object_type="job",
        object_id=job.id,
        object_name=job.project_name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
        details={"runner_id": runner.id, "runner_name": runner.name},
    )
    execution_data_url = url_for(
        "main.runner_job_execution_data_api",
        job_id=job.id,
        _external=True,
    )
    return jsonify(
        job_assignment_manifest(
            job, token, repository_artifacts, execution_data_url
        )
    )


@bp.get("/api/runners/jobs/<int:job_id>/execution-data")
@csrf.exempt
def runner_job_execution_data_api(job_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    token = _dispatch_token()
    if (
        job.assigned_runner_id != runner.id
        or not job.dispatch_token
        or not secrets.compare_digest(job.dispatch_token, token)
    ):
        return jsonify({"error": "assignment_mismatch"}), 403
    try:
        envelope = encrypt_execution_data(job, runner, token)
    except RunnerExecutionDataError:
        return jsonify({"error": "execution_data_preparation_failed"}), 500
    response = jsonify(envelope)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.get("/api/runners/jobs/<int:job_id>/repositories/<int:snapshot_id>/artifact")
@csrf.exempt
def runner_job_repository_artifact_api(job_id, snapshot_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    if (
        job.assigned_runner_id != runner.id
        or not job.dispatch_token
        or not secrets.compare_digest(job.dispatch_token, _dispatch_token())
    ):
        return jsonify({"error": "assignment_mismatch"}), 403
    snapshot = next(
        (item for item in job.repository_snapshots if item.id == snapshot_id),
        None,
    )
    if snapshot is None:
        return jsonify({"error": "Repository snapshot not found."}), 404
    try:
        path = repository_artifact_path(snapshot)
    except RunnerArtifactError:
        return jsonify({"error": "Repository artefact path is invalid."}), 404
    if not path.is_file():
        return jsonify({"error": "Repository artefact is unavailable."}), 404
    return send_file(
        path,
        mimetype="application/gzip",
        as_attachment=True,
        download_name=path.name,
        conditional=True,
    )

@bp.get("/api/runners/slices/<int:slice_id>/execution-data")
@csrf.exempt
def runner_slice_execution_data_api(slice_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    token = _dispatch_token()
    if not slice_assignment_matches(execution_slice, runner, token):
        return jsonify({"error": "assignment_mismatch"}), 403
    try:
        envelope = encrypt_execution_data(execution_slice.step.job, runner, token)
    except RunnerExecutionDataError:
        return jsonify({"error": "execution_data_preparation_failed"}), 500
    response = jsonify(envelope)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.get("/api/runners/slices/<int:slice_id>/repositories/<int:snapshot_id>/artifact")
@csrf.exempt
def runner_slice_repository_artifact_api(slice_id, snapshot_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    if not slice_assignment_matches(execution_slice, runner, _dispatch_token()):
        return jsonify({"error": "assignment_mismatch"}), 403
    job = execution_slice.step.job
    snapshot = next(
        (item for item in job.repository_snapshots if item.id == snapshot_id),
        None,
    )
    if snapshot is None:
        return jsonify({"error": "Repository snapshot not found."}), 404
    try:
        path = repository_artifact_path(snapshot)
    except RunnerArtifactError:
        return jsonify({"error": "Repository artefact path is invalid."}), 404
    if not path.is_file():
        return jsonify({"error": "Repository artefact is unavailable."}), 404
    return send_file(
        path,
        mimetype="application/gzip",
        as_attachment=True,
        download_name=path.name,
        conditional=True,
    )


def _authenticated_runner_request():
    runner_uuid = str(request.headers.get("X-Journeyman-Runner-ID") or "")
    authorization = str(request.headers.get("Authorization") or "")
    secret = authorization[7:] if authorization.startswith("Bearer ") else ""
    return authenticate_runner(runner_uuid, secret)


def _dispatch_token():
    return str(request.headers.get("X-Journeyman-Dispatch-Token") or "")


@bp.post("/api/runners/slices/<int:slice_id>/start")
@csrf.exempt
def runner_slice_start_api(slice_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    accepted, result = start_remote_slice(execution_slice, runner, _dispatch_token())
    if not accepted:
        return jsonify({"error": result}), 403 if result == "assignment_mismatch" else 409
    return jsonify({"slice_id": slice_id, "status": "running", "result": result})


@bp.get("/api/runners/slices/<int:slice_id>/control")
@csrf.exempt
def runner_slice_control_api(slice_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    result = remote_slice_control(execution_slice, runner, _dispatch_token())
    if result is None:
        return jsonify({"error": "assignment_mismatch"}), 403
    return jsonify(result)


@bp.post("/api/runners/slices/<int:slice_id>/output")
@csrf.exempt
def runner_slice_output_api(slice_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    payload, error = _json_object_payload()
    if error is not None:
        return error
    accepted, result = update_remote_slice_output(
        execution_slice,
        runner,
        _dispatch_token(),
        payload,
    )
    if not accepted:
        return jsonify({"error": result}), 403 if result == "assignment_mismatch" else 409
    return jsonify({"slice_id": slice_id, "status": execution_slice.status, "result": result})


@bp.post("/api/runners/slices/<int:slice_id>/complete")
@csrf.exempt
def runner_slice_complete_api(slice_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None:
        return jsonify({"error": "Execution slice not found."}), 404
    payload, error = _json_object_payload()
    if error is not None:
        return error
    accepted, result = complete_remote_slice(
        execution_slice, runner, _dispatch_token(), payload
    )
    if not accepted:
        return jsonify({"error": result}), 403 if result == "assignment_mismatch" else 409
    return jsonify({"slice_id": slice_id, "status": execution_slice.status, "result": result})


@bp.post("/api/runners/jobs/<int:job_id>/start")
@csrf.exempt
def runner_job_start_api(job_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    accepted, result = start_remote_job(job, runner, _dispatch_token())
    if not accepted:
        status = 403 if result == "assignment_mismatch" else 409
        return jsonify({"error": result}), status
    record_audit_event(
        "runner.job.start",
        result="running",
        object_type="job",
        object_id=job.id,
        object_name=job.project_name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
        details={"runner_id": runner.id, "runner_name": runner.name},
    )
    return jsonify({"job_id": job.id, "status": "running", "result": result})


@bp.get("/api/runners/jobs/<int:job_id>/control")
@csrf.exempt
def runner_job_control_api(job_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    result = remote_job_control(job, runner, _dispatch_token())
    if result is None:
        return jsonify({"error": "assignment_mismatch"}), 403
    return jsonify(result)


@bp.post(
    "/api/runners/jobs/<int:job_id>/refresh-inventories"
)
@csrf.exempt
def runner_job_refresh_inventories_api(job_id):
    runner = _authenticated_runner_request()

    if runner is None:
        return jsonify(
            {"error": "Runner authentication failed."}
        ), 403

    job = db.session.get(
        Job,
        job_id,
    )

    if job is None:
        return jsonify(
            {"error": "Job not found."}
        ), 404

    token = _dispatch_token()

    from app.services.runner_job_lifecycle import (
        assignment_matches,
    )

    if not assignment_matches(
        job,
        runner,
        token,
    ):
        return jsonify(
            {"error": "assignment_mismatch"}
        ), 403

    payload, error = _json_object_payload()
    if error is not None:
        return error

    try:
        position = int(
            payload.get("position")
        )
    except (TypeError, ValueError):
        return jsonify(
            {"error": "invalid_position"}
        ), 400

    step = next(
        (
            item
            for item in job.steps
            if item.position == position
        ),
        None,
    )

    if step is None:
        return jsonify(
            {"error": "unknown_step"}
        ), 404

    if not step.refresh_inventory_after:
        return jsonify(
            {"error": "refresh_not_enabled"}
        ), 409

    try:
        snapshots = (
            refresh_job_inventories_after_step(
                job,
                step,
            )
        )

        envelope = encrypt_execution_data(
            job,
            runner,
            token,
        )

    except JobInventoryRefreshError as exc:
        return jsonify(
            {"error": str(exc)}
        ), 409

    except RunnerExecutionDataError:
        return jsonify(
            {
                "error":
                "execution_data_preparation_failed"
            }
        ), 500

    record_audit_event(
        "runner.job.inventory_refresh",
        result="successful",
        object_type="job",
        object_id=job.id,
        object_name=job.project_name,
        actor_username="runner:{}".format(
            runner.runner_uuid
        ),
        authenticated_via="runner-token",
        details={
            "runner_id": runner.id,
            "runner_name": runner.name,
            "trigger_step": step.position,
            "snapshot_ids": [
                snapshot.id
                for snapshot in snapshots
            ],
        },
    )

    return jsonify(
        {
            "job_id": job.id,
            "trigger_step": step.position,
            "refreshed_snapshot_ids": [
                snapshot.id
                for snapshot in snapshots
            ],
            "execution_data": envelope,
        }
    )


@bp.post("/api/runners/jobs/<int:job_id>/complete")
@csrf.exempt
def runner_job_complete_api(job_id):
    runner = _authenticated_runner_request()
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    payload, error = _json_object_payload()
    if error is not None:
        return error
    accepted, result = complete_remote_job(
        job, runner, _dispatch_token(), payload
    )
    if not accepted:
        status = 403 if result == "assignment_mismatch" else 409
        if result.startswith("invalid_") or result == "unknown_step":
            status = 400
        return jsonify({"error": result}), status
    try:
        cleanup_job_repository_artifacts(job.id)
    except RunnerArtifactError:
        pass
    record_audit_event(
        "runner.job.complete",
        result=job.status,
        object_type="job",
        object_id=job.id,
        object_name=job.project_name,
        actor_username="runner:{}".format(runner.runner_uuid),
        authenticated_via="runner-token",
        details={
            "runner_id": runner.id,
            "runner_name": runner.name,
            "status": job.status,
            "exit_code": job.exit_code,
        },
    )
    return jsonify({"job_id": job.id, "status": job.status, "result": result})
