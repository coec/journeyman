"""Schedule validation, next-run calculation, and worker dispatch."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy import or_, update

from app import db
from app.models import ProjectSchedule
from app.services.audit import record_audit_event
from app.services.project_execution import ProjectExecutionQueueError, queue_project_execution

VALID_SCHEDULE_TYPES = frozenset({"once", "daily", "weekly", "interval"})
WEEKDAY_VALUES = frozenset(range(7))


class ScheduleValidationError(ValueError):
    pass


def _aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_local_datetime(value, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError("Unknown timezone.") from exc
    try:
        local = datetime.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ScheduleValidationError("Start date and time is invalid.") from exc
    if local.tzinfo is not None:
        local = local.replace(tzinfo=None)
    return local.replace(tzinfo=zone).astimezone(timezone.utc)


def validate_schedule_values(schedule_type, interval_minutes, weekdays, start_at=None, end_at=None):
    errors = []
    if schedule_type not in VALID_SCHEDULE_TYPES:
        errors.append("Schedule type is invalid.")
    if schedule_type == "interval":
        if interval_minutes is None or not 1 <= interval_minutes <= 525600:
            errors.append("Interval must be between 1 and 525600 minutes.")
    if schedule_type == "weekly" and not weekdays:
        errors.append("Select at least one weekday for a weekly schedule.")
    if any(day not in WEEKDAY_VALUES for day in weekdays):
        errors.append("One or more weekdays are invalid.")
    if schedule_type != "once" and start_at is not None and end_at is not None and end_at <= start_at:
        errors.append("End date and time must be after the start date and time.")
    return errors


def calculate_next_run(schedule, after=None):
    after = _aware_utc(after or datetime.now(timezone.utc))
    start = _aware_utc(schedule.start_at)
    end = _aware_utc(schedule.end_at)
    if schedule.schedule_type == "once":
        return start if start > after else None
    if schedule.schedule_type == "interval":
        interval = timedelta(minutes=int(schedule.interval_minutes))
        if start > after:
            return start if end is None or start <= end else None
        elapsed = after - start
        steps = int(elapsed.total_seconds() // interval.total_seconds()) + 1
        candidate = start + (interval * steps)
        return candidate if end is None or candidate <= end else None

    zone = ZoneInfo(schedule.timezone_name)
    local_start = start.astimezone(zone)
    local_after = after.astimezone(zone)
    target_time = local_start.timetz().replace(tzinfo=None)
    candidate_date = max(local_after.date(), local_start.date())

    for offset in range(0, 15):
        date_value = candidate_date + timedelta(days=offset)
        if schedule.schedule_type == "weekly":
            allowed = {int(value) for value in schedule.weekdays.split(",") if value != ""}
            if date_value.weekday() not in allowed:
                continue
        candidate = datetime.combine(date_value, target_time, zone)
        candidate_utc = candidate.astimezone(timezone.utc)
        if candidate_utc > after and candidate_utc >= start:
            return candidate_utc if end is None or candidate_utc <= end else None
    return None


def due_schedule_ids(now=None, limit=50):
    now = _aware_utc(now or datetime.now(timezone.utc))
    return [row.id for row in (
        ProjectSchedule.query
        .filter(ProjectSchedule.enabled.is_(True), ProjectSchedule.next_run_at.is_not(None), ProjectSchedule.next_run_at <= now)
        .order_by(ProjectSchedule.next_run_at.asc(), ProjectSchedule.id.asc())
        .limit(limit)
        .all()
    )]


def claim_schedule(schedule_id, now=None, stale_after_minutes=30):
    now = _aware_utc(now or datetime.now(timezone.utc))
    stale_before = now - timedelta(minutes=stale_after_minutes)
    result = db.session.execute(
        update(ProjectSchedule)
        .where(
            ProjectSchedule.id == schedule_id,
            ProjectSchedule.enabled.is_(True),
            ProjectSchedule.next_run_at.is_not(None),
            ProjectSchedule.next_run_at <= now,
            or_(ProjectSchedule.claimed_at.is_(None), ProjectSchedule.claimed_at < stale_before),
        )
        .values(claimed_at=now)
    )
    db.session.commit()
    return result.rowcount == 1


def run_claimed_schedule(schedule_id, now=None):
    now = _aware_utc(now or datetime.now(timezone.utc))
    schedule = db.session.get(ProjectSchedule, schedule_id)
    if schedule is None or schedule.claimed_at is None:
        return None
    try:
        job = queue_project_execution(
            project=schedule.project,
            requested_by=schedule.created_by,
            message='Queued by schedule "{}".'.format(schedule.name),
        )
        schedule.last_run_at = now
        schedule.last_job_id = job.id
        schedule.last_error = ""
        schedule.next_run_at = calculate_next_run(schedule, after=now)
        if schedule.schedule_type == "once" or schedule.next_run_at is None:
            schedule.enabled = False
        schedule.claimed_at = None
        db.session.commit()
        record_audit_event(
            "schedule.launch",
            object_type="project_schedule",
            object_id=schedule.id,
            object_name=schedule.name,
            actor_username=schedule.created_by,
            details={"project_id": schedule.project_id, "job_id": job.id},
        )
        return job
    except ProjectExecutionQueueError as exc:
        schedule.last_error = str(exc)
        schedule.claimed_at = None
        schedule.next_run_at = calculate_next_run(schedule, after=now)
        if schedule.schedule_type == "once" or schedule.next_run_at is None:
            schedule.enabled = False
        db.session.commit()
        record_audit_event(
            "schedule.launch",
            result="failure",
            object_type="project_schedule",
            object_id=schedule.id,
            object_name=schedule.name,
            actor_username=schedule.created_by,
            details={"project_id": schedule.project_id, "error": str(exc)},
        )
        current_app.logger.warning("Scheduled Project launch failed: %s", exc)
        return None


def run_due_schedules(now=None, limit=50):
    launched = []
    for schedule_id in due_schedule_ids(now=now, limit=limit):
        if claim_schedule(schedule_id, now=now):
            job = run_claimed_schedule(schedule_id, now=now)
            if job is not None:
                launched.append(job)
    return launched
