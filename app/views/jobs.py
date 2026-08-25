"""Job listing, detail, cancellation, and live-update routes."""

import json
import time

from flask import Response, jsonify, stream_with_context

from app.models import JobStep, JobStepExecutionSlice
from app.services.job_output_export import build_job_output_export
from app.services.job_cancellation import cancel_job
from app.services.job_rerun import (
    RERUN_SCOPE_ALL,
    RERUN_SCOPE_FAILED,
    TERMINAL_JOB_STATUSES,
    JobRerunError,
    failed_hosts_for_rerun,
    normalise_rerun_scope,
    rerun_job,
    rerun_preflight_issues,
)
from app.services.pagination import page_size_for_user
from app.services.oversight_display import build_oversight_rows
from app.services.project_oversight import approve_current_oversight
from app.routes import (
    Job, _utcnow, abort, bp, can_view_job, current_user_is_admin,
    current_username, db, flash, record_audit_event, redirect,
    render_template, request, url_for,
)

def _visible_jobs_query():
    """Return the Job query visible to the current request subject."""

    query = Job.query

    if not current_user_is_admin():
        query = query.filter(
            Job.requested_by == current_username()
        )

    return query


def _jobs_list_fingerprint():
    """Return lightweight state for recent visible Jobs."""
    return tuple(
        _visible_jobs_query()
        .with_entities(
            Job.id, Job.status, Job.queued_at, Job.started_at, Job.finished_at
        )
        .order_by(Job.id.desc())
        .limit(500)
        .all()
    )


@bp.get("/jobs")
def jobs():
    per_page = page_size_for_user(current_username())
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    pagination = (
        _visible_jobs_query()
        .order_by(Job.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    failed_rerun_hosts_by_job_id = {
        job.id: failed_hosts_for_rerun(job)
        for job in pagination.items
        if job.status in TERMINAL_JOB_STATUSES and job.status != "successful"
    }
    return render_template(
        "jobs.html",
        jobs=pagination.items,
        pagination=pagination,
        failed_rerun_hosts_by_job_id=failed_rerun_hosts_by_job_id,
    )


@bp.get("/jobs/events")
def jobs_events():
    """Notify the Jobs page when its visible table data has changed."""

    initial_fingerprint = _jobs_list_fingerprint()

    @stream_with_context
    def generate():
        last_fingerprint = initial_fingerprint
        heartbeat_counter = 0

        while True:
            db.session.expire_all()
            fingerprint = _jobs_list_fingerprint()

            if fingerprint != last_fingerprint:
                yield "event: jobs-update\ndata: {}\n\n"
                return

            heartbeat_counter += 1
            if heartbeat_counter >= 15:
                yield ": heartbeat\n\n"
                heartbeat_counter = 0

            time.sleep(1)

    response = Response(
        generate(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/jobs/<int:job_id>/output")
def job_output_download(job_id):
    job = db.get_or_404(Job, job_id)

    if not can_view_job(job):
        abort(403)

    export = build_job_output_export(job)
    response = Response(export.data, mimetype=export.mimetype)
    response.headers["Content-Disposition"] = (
        'attachment; filename="{}"'.format(export.filename)
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/jobs/<int:job_id>")
def job_detail(job_id):
    job = db.get_or_404(Job, job_id)

    if not can_view_job(job):
        abort(403)

    steps = list(job.steps)
    total_steps = len(steps)

    finished_step_statuses = {
        "successful",
        "failed",
        "cancelled",
    }

    completed_steps = sum(
        1
        for step in steps
        if step.status in finished_step_statuses
    )

    current_step = next(
        (
            step
            for step in steps
            if step.status == "running"
        ),
        None,
    )

    if current_step is None:
        current_step = next(
            (
                step
                for step in steps
                if step.status == "pending"
            ),
            None,
        )

    progress_percent = 0

    if total_steps:
        progress_percent = int(
            completed_steps / total_steps * 100
        )

    elapsed_seconds = 0

    if job.started_at:
        elapsed_seconds = job.duration_seconds or 0

    elapsed_hours, remainder = divmod(
        elapsed_seconds,
        3600,
    )

    elapsed_minutes, elapsed_seconds = divmod(
        remainder,
        60,
    )

    elapsed_display = (
        f"{elapsed_hours:02d}:"
        f"{elapsed_minutes:02d}:"
        f"{elapsed_seconds:02d}"
    )

    return render_template(
        "job_detail.html",
        job=job,
        total_steps=total_steps,
        completed_steps=completed_steps,
        current_step=current_step,
        progress_percent=progress_percent,
        elapsed_display=elapsed_display,
        failed_rerun_hosts=failed_hosts_for_rerun(job),
    )


def _job_live_fingerprint(job):
    """Return a compact fingerprint for fields rendered on Job detail."""

    return json.dumps(
        {
            "status": job.status,
            "message": job.message,
            "started_at": (
                job.started_at.isoformat()
                if job.started_at else None
            ),
            "finished_at": (
                job.finished_at.isoformat()
                if job.finished_at else None
            ),
            "exit_code": job.exit_code,
            "steps": [
                {
                    "id": step.id,
                    "status": step.status,
                    "started_at": (
                        step.started_at.isoformat()
                        if step.started_at else None
                    ),
                    "finished_at": (
                        step.finished_at.isoformat()
                        if step.finished_at else None
                    ),
                    "exit_code": step.exit_code,
                    "command": step.command or "",
                    "stdout": step.stdout or "",
                    "stderr": step.stderr or "",
                    "custom_stats": step.custom_stats_json or "",
                    "execution_slices": [
                        {
                            "id": item.id,
                            "status": item.status,
                            "message": item.message or "",
                        }
                        for item in step.execution_slices
                    ],
                }
                for step in job.steps
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _can_review_oversight(job):
    return bool(
        current_user_is_admin()
        or (
            job.oversight_reviewer
            and job.oversight_reviewer == current_username()
        )
    )


@bp.get("/jobs/<int:job_id>/oversight")
def job_oversight(job_id):
    job = db.get_or_404(Job, job_id)
    if not can_view_job(job):
        abort(403)
    if job.status != "waiting_oversight":
        flash("This Job is not waiting for oversight.", "error")
        return redirect(url_for("main.job_detail", job_id=job.id))

    return render_template(
        "job_oversight.html",
        job=job,
        oversight_rows=build_oversight_rows(job),
        can_review=_can_review_oversight(job),
    )


@bp.post("/jobs/<int:job_id>/oversight/continue")
def job_oversight_continue(job_id):
    job = db.get_or_404(Job, job_id)
    if not can_view_job(job) or not _can_review_oversight(job):
        abort(403)

    approved = approve_current_oversight(job)
    if not approved:
        flash("This Job no longer has a pending oversight decision.", "error")
        db.session.rollback()
        return redirect(url_for("main.job_detail", job_id=job.id))

    db.session.commit()
    record_audit_event(
        "job.oversight.continue",
        object_type="job",
        object_id=job.id,
        details={
            "reviewer": current_username(),
            "step_positions": [step.position for step in approved],
        },
    )
    flash(
        "Oversight completed. Step(s) {} may proceed.".format(
            ", ".join(str(step.position) for step in approved)
        ),
        "success",
    )
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.get("/jobs/<int:job_id>/steps/<int:step_id>/output")
def job_step_output(job_id, step_id):
    job = db.get_or_404(Job, job_id)
    if not can_view_job(job):
        abort(403)

    step = db.session.get(JobStep, step_id)
    if step is None or step.job_id != job.id:
        abort(404)

    response = jsonify({
        "step_id": step.id,
        "status": step.status,
        "terminal": step.status in {
            "successful", "failed", "cancelled"
        },
        "runner": "Built-in local runner",
        "command": step.command or "",
        "stdout": step.stdout or "",
        "stderr": step.stderr or "",
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/jobs/<int:job_id>/slices/<int:slice_id>/output")
def job_slice_output(job_id, slice_id):
    job = db.get_or_404(Job, job_id)
    if not can_view_job(job):
        abort(403)

    execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
    if execution_slice is None or execution_slice.step.job_id != job.id:
        abort(404)

    status = execution_slice.status
    command = execution_slice.command or ""
    stdout = execution_slice.stdout or ""
    stderr = execution_slice.stderr or ""

    # Older single-runner Jobs could contain a planned slice that was never
    # executed because the legacy direct Job path ran the JobStep instead.
    # Preserve useful output for those historical Jobs rather than leaving the
    # modal permanently on "Loading..." with a stale pending slice.
    step = execution_slice.step
    if (
        status in {"pending", "assigned"}
        and step.status in {"successful", "failed", "cancelled"}
        and not command
        and not stdout
        and not stderr
    ):
        status = step.status
        command = step.command or ""
        stdout = step.stdout or ""
        stderr = step.stderr or ""

    response = jsonify({
        "slice_id": execution_slice.id,
        "status": status,
        "terminal": status in {
            "successful", "failed", "cancelled"
        },
        "runner": (
            execution_slice.runner_hostname
            or execution_slice.runner_name
            or ("Local runner" if execution_slice.dispatch_target == "local" else "Remote runner")
        ),
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/jobs/<int:job_id>/events")
def job_events(job_id):
    job = db.get_or_404(Job, job_id)

    if not can_view_job(job):
        abort(403)

    @stream_with_context
    def generate():
        last_fingerprint = None
        heartbeat_counter = 0

        while True:
            db.session.expire_all()
            current_job = db.session.get(Job, job_id)

            if current_job is None:
                yield "event: job-removed\ndata: {}\n\n"
                return

            fingerprint = _job_live_fingerprint(current_job)

            if fingerprint != last_fingerprint:
                payload = json.dumps(
                    {
                        "status": current_job.status,
                        "terminal": current_job.status in {
                            "successful",
                            "failed",
                            "cancelled",
                        },
                    }
                )
                yield "event: job-update\ndata: {}\n\n".format(payload)
                last_fingerprint = fingerprint
                heartbeat_counter = 0

                if current_job.status in {
                    "successful",
                    "failed",
                    "cancelled",
                }:
                    return
            else:
                heartbeat_counter += 1
                if heartbeat_counter >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

            time.sleep(1)

    response = Response(
        generate(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _rerun_preview_steps(job, rerun_scope=RERUN_SCOPE_ALL):
    """Describe the immutable execution snapshot without exposing host variables."""
    rows = []
    failed_hosts = (
        set(failed_hosts_for_rerun(job))
        if rerun_scope == RERUN_SCOPE_FAILED
        else None
    )
    for step in sorted(job.steps, key=lambda item: item.position):
        hosts = sorted(
            {
                host
                for execution_slice in step.execution_slices
                for host in execution_slice.get_hosts()
                if failed_hosts is None or host in failed_hosts
            }
        )
        rows.append(
            {
                "position": step.position,
                "name": step.name or "Step {}".format(step.position),
                "repository": (
                    step.repository_snapshot.repository_name
                    if step.repository_snapshot is not None
                    else ""
                ),
                "commit": (
                    step.repository_snapshot.repository_commit
                    if step.repository_snapshot is not None
                    else ""
                ),
                "inventory": (
                    step.inventory_snapshot.inventory_name
                    if step.inventory_snapshot is not None
                    else ""
                ),
                "host_count": (
                    len(hosts)
                    if rerun_scope == RERUN_SCOPE_FAILED or hosts
                    else (
                        step.inventory_snapshot.host_count
                        if step.inventory_snapshot is not None
                        else 0
                    )
                ),
                "hosts": hosts,
                "limit": (
                    ",".join(hosts)
                    if rerun_scope == RERUN_SCOPE_FAILED
                    else step.limit or ""
                ),
            }
        )
    return rows


@bp.route("/jobs/<int:job_id>/rerun", methods=["GET", "POST"])
def job_rerun(job_id):
    job = db.get_or_404(Job, job_id)

    if not can_view_job(job):
        abort(403)

    if job.status not in TERMINAL_JOB_STATUSES:
        flash(
            "Job #{} cannot be rerun until it has finished.".format(job.id),
            "error",
        )
        return redirect(url_for("main.job_detail", job_id=job.id))

    requested_scope = (
        request.form.get("rerun_scope")
        if request.method == "POST"
        else request.args.get("scope")
    )
    try:
        rerun_scope = normalise_rerun_scope(requested_scope)
    except JobRerunError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.job_detail", job_id=job.id))

    failed_rerun_hosts = failed_hosts_for_rerun(job)
    if rerun_scope == RERUN_SCOPE_FAILED and not failed_rerun_hosts:
        flash(
            "Job #{} has no saved failed or unreachable hosts that can be rerun."
            .format(job.id),
            "error",
        )
        return redirect(url_for("main.job_detail", job_id=job.id))

    rerun_blockers = rerun_preflight_issues(job)
    preview_steps = _rerun_preview_steps(job, rerun_scope)

    if request.method == "GET":
        return render_template(
            "job_rerun_preview.html",
            job=job,
            preview_steps=preview_steps,
            rerun_blockers=rerun_blockers,
            rerun_scope=rerun_scope,
            failed_rerun_hosts=failed_rerun_hosts,
        )

    if rerun_blockers:
        return render_template(
            "job_rerun_preview.html",
            job=job,
            preview_steps=preview_steps,
            rerun_blockers=rerun_blockers,
            rerun_scope=rerun_scope,
            failed_rerun_hosts=failed_rerun_hosts,
            warning="This saved execution snapshot is no longer runnable.",
        ), 409

    if request.form.get("confirm_rerun") != "yes":
        return render_template(
            "job_rerun_preview.html",
            job=job,
            preview_steps=preview_steps,
            rerun_blockers=rerun_blockers,
            rerun_scope=rerun_scope,
            failed_rerun_hosts=failed_rerun_hosts,
            warning="Review the saved execution snapshot before confirming the rerun.",
        ), 400

    try:
        result = rerun_job(
            job,
            requested_by=current_username(),
            source="Journeyman web interface",
            scope=rerun_scope,
        )
    except JobRerunError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.job_detail", job_id=job.id))

    record_audit_event(
        "job.rerun",
        object_type="job",
        object_id=result.job.id,
        details={
            "source_job_id": job.id,
            "requested_by": current_username(),
            "preview_confirmed": True,
            "rerun_scope": rerun_scope,
            "failed_host_count": (
                len(failed_rerun_hosts)
                if rerun_scope == RERUN_SCOPE_FAILED
                else None
            ),
        },
    )
    flash(
        (
            "Job #{} queued as failed-host-only rerun of Job #{}."
            if rerun_scope == RERUN_SCOPE_FAILED
            else "Job #{} queued as rerun of Job #{}."
        ).format(result.job.id, job.id),
        "success",
    )
    return redirect(url_for("main.job_detail", job_id=result.job.id))

@bp.post("/jobs/<int:job_id>/cancel")
def job_cancel(job_id):
    job = db.get_or_404(Job, job_id)

    if not can_view_job(job):
        abort(403)

    result = cancel_job(job, source="Journeyman web interface")
    flash(
        result.message,
        "success" if result.changed else (
            "warning" if result.status == "cancelling" else "error"
        ),
    )

    return redirect(
        url_for("main.job_detail", job_id=job.id)
    )
