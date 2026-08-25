"""Project scheduling administration routes."""

from datetime import datetime, timezone

from app.routes import (
    Project, abort, bp, current_user_is_admin, current_username, db, flash,
    redirect, render_template, request, url_for,
)
from app.models import ProjectSchedule
from app.services.audit import record_audit_event
from app.services.project_execution import ProjectExecutionQueueError, queue_project_execution
from app.services.name_ordering import reserved_name_ordering
from app.services.pagination import paginate_list, page_size_for_user
from app.services.schedules import (
    calculate_next_run,
    parse_local_datetime,
    validate_schedule_values,
    ScheduleValidationError,
)


def _require_admin():
    if not current_user_is_admin():
        abort(403)


def _form_values(schedule=None, project_id=None):
    if schedule is not None:
        start = schedule.start_at
        end = schedule.end_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(schedule.timezone_name)
            start = start.astimezone(zone)
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                end = end.astimezone(zone)
        except Exception:
            start = start.astimezone(timezone.utc)
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                end = end.astimezone(timezone.utc)
        return {
            "name": schedule.name,
            "project_id": schedule.project_id,
            "schedule_type": schedule.schedule_type,
            "timezone_name": schedule.timezone_name,
            "start_at": start.strftime("%Y-%m-%dT%H:%M"),
            "end_at": end.strftime("%Y-%m-%dT%H:%M") if end is not None else "",
            "interval_minutes": schedule.interval_minutes or 60,
            "weekdays": {int(v) for v in schedule.weekdays.split(",") if v != ""},
            "enabled": schedule.enabled,
        }
    return {
        "name": "",
        "project_id": project_id,
        "schedule_type": "once",
        "timezone_name": "Australia/Perth",
        "start_at": "",
        "end_at": "",
        "interval_minutes": 60,
        "weekdays": set(),
        "enabled": True,
    }


def _submitted_values():
    try:
        project_id = int(request.form.get("project_id", ""))
    except (TypeError, ValueError):
        project_id = None
    try:
        interval_minutes = int(request.form.get("interval_minutes", ""))
    except (TypeError, ValueError):
        interval_minutes = None
    weekdays = set()
    for value in request.form.getlist("weekdays"):
        try:
            weekdays.add(int(value))
        except (TypeError, ValueError):
            weekdays.add(-1)
    return {
        "name": str(request.form.get("name") or "").strip(),
        "project_id": project_id,
        "schedule_type": str(request.form.get("schedule_type") or "").strip(),
        "timezone_name": str(request.form.get("timezone_name") or "").strip(),
        "start_at": str(request.form.get("start_at") or "").strip(),
        "end_at": str(request.form.get("end_at") or "").strip(),
        "interval_minutes": interval_minutes,
        "weekdays": weekdays,
        "enabled": request.form.get("enabled") == "on",
    }


def _apply(schedule, values):
    schedule.name = values["name"]
    schedule.project_id = values["project_id"]
    schedule.schedule_type = values["schedule_type"]
    schedule.timezone_name = values["timezone_name"]
    schedule.start_at = parse_local_datetime(values["start_at"], values["timezone_name"])
    schedule.end_at = (
        parse_local_datetime(values["end_at"], values["timezone_name"])
        if values["schedule_type"] != "once" and values["end_at"]
        else None
    )
    schedule.interval_minutes = values["interval_minutes"] if values["schedule_type"] == "interval" else None
    schedule.weekdays = ",".join(str(v) for v in sorted(values["weekdays"])) if values["schedule_type"] == "weekly" else ""
    schedule.enabled = values["enabled"]
    schedule.next_run_at = calculate_next_run(schedule) if schedule.enabled else None
    if schedule.enabled and schedule.next_run_at is None:
        schedule.enabled = False
    schedule.claimed_at = None


@bp.get("/schedules")
def schedules():
    _require_admin()
    rows = ProjectSchedule.query.order_by(*reserved_name_ordering(ProjectSchedule.name)).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    return render_template("schedules.html", schedules=pagination.items, pagination=pagination)


@bp.route("/schedules/new", methods=["GET", "POST"])
def schedule_new():
    _require_admin()
    project_id = request.args.get("project_id", type=int)
    values = _form_values(project_id=project_id)
    projects = Project.query.order_by(Project.name.asc()).all()
    if request.method == "POST":
        values = _submitted_values()
        errors = []
        if not values["name"]:
            errors.append("Name is required.")
        project = db.session.get(Project, values["project_id"]) if values["project_id"] else None
        if project is None:
            errors.append("Project is required.")
        start_at = None
        end_at = None
        try:
            start_at = parse_local_datetime(values["start_at"], values["timezone_name"])
            if values["schedule_type"] != "once" and values["end_at"]:
                end_at = parse_local_datetime(values["end_at"], values["timezone_name"])
        except ScheduleValidationError as exc:
            errors.append(str(exc))
        errors.extend(validate_schedule_values(
            values["schedule_type"],
            values["interval_minutes"],
            values["weekdays"],
            start_at=start_at,
            end_at=end_at,
        ))
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("schedule_form.html", schedule=None, projects=projects, form_data=values)
        schedule = ProjectSchedule(created_by=current_username())
        _apply(schedule, values)
        db.session.add(schedule)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Unable to create the schedule. The name may already be in use for this Project.", "error")
            return render_template("schedule_form.html", schedule=None, projects=projects, form_data=values)
        record_audit_event("schedule.create", object_type="project_schedule", object_id=schedule.id, object_name=schedule.name, details={"project_id": schedule.project_id})
        flash("Schedule created.", "success")
        return redirect(url_for("main.schedules"))
    return render_template("schedule_form.html", schedule=None, projects=projects, form_data=values)


@bp.route("/schedules/<int:schedule_id>/edit", methods=["GET", "POST"])
def schedule_edit(schedule_id):
    _require_admin()
    schedule = db.get_or_404(ProjectSchedule, schedule_id)
    projects = Project.query.order_by(Project.name.asc()).all()
    values = _form_values(schedule=schedule)
    if request.method == "POST":
        values = _submitted_values()
        errors = []
        if not values["name"]:
            errors.append("Name is required.")
        if db.session.get(Project, values["project_id"]) is None:
            errors.append("Project is required.")
        start_at = None
        end_at = None
        try:
            start_at = parse_local_datetime(values["start_at"], values["timezone_name"])
            if values["schedule_type"] != "once" and values["end_at"]:
                end_at = parse_local_datetime(values["end_at"], values["timezone_name"])
        except ScheduleValidationError as exc:
            errors.append(str(exc))
        errors.extend(validate_schedule_values(
            values["schedule_type"],
            values["interval_minutes"],
            values["weekdays"],
            start_at=start_at,
            end_at=end_at,
        ))
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("schedule_form.html", schedule=schedule, projects=projects, form_data=values)
        _apply(schedule, values)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Unable to update the schedule.", "error")
            return render_template("schedule_form.html", schedule=schedule, projects=projects, form_data=values)
        record_audit_event("schedule.update", object_type="project_schedule", object_id=schedule.id, object_name=schedule.name, details={"project_id": schedule.project_id})
        flash("Schedule updated.", "success")
        return redirect(url_for("main.schedules"))
    return render_template("schedule_form.html", schedule=schedule, projects=projects, form_data=values)


@bp.post("/schedules/<int:schedule_id>/toggle")
def schedule_toggle(schedule_id):
    _require_admin()
    schedule = db.get_or_404(ProjectSchedule, schedule_id)
    requested_enabled = not schedule.enabled
    schedule.next_run_at = calculate_next_run(schedule) if requested_enabled else None
    schedule.enabled = requested_enabled and schedule.next_run_at is not None
    schedule.claimed_at = None
    db.session.commit()
    if requested_enabled and not schedule.enabled:
        flash("Schedule cannot be enabled because it has no future run before its end date and time.", "error")
    else:
        record_audit_event("schedule.enable" if schedule.enabled else "schedule.disable", object_type="project_schedule", object_id=schedule.id, object_name=schedule.name)
        flash("Schedule {}.".format("enabled" if schedule.enabled else "disabled"), "success")
    return redirect(url_for("main.schedules"))


@bp.post("/schedules/<int:schedule_id>/run-now")
def schedule_run_now(schedule_id):
    _require_admin()
    schedule = db.get_or_404(ProjectSchedule, schedule_id)
    try:
        job = queue_project_execution(project=schedule.project, requested_by=current_username(), message='Dispatch now from schedule "{}".'.format(schedule.name))
    except ProjectExecutionQueueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.schedules"))
    schedule.last_run_at = datetime.now(timezone.utc)
    schedule.last_job_id = job.id
    schedule.last_error = ""
    db.session.commit()
    record_audit_event("schedule.run_now", object_type="project_schedule", object_id=schedule.id, object_name=schedule.name, details={"job_id": job.id})
    flash("Job #{} dispatched.".format(job.id), "success")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.post("/schedules/<int:schedule_id>/delete")
def schedule_delete(schedule_id):
    _require_admin()
    schedule = db.get_or_404(ProjectSchedule, schedule_id)
    name = schedule.name
    db.session.delete(schedule)
    db.session.commit()
    record_audit_event("schedule.delete", object_type="project_schedule", object_id=schedule_id, object_name=name)
    flash('Schedule "{}" deleted.'.format(name), "success")
    return redirect(url_for("main.schedules"))
