"""Declarative Project schedule configuration shared by API clients."""

from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

from app import db
from app.models import Project, ProjectSchedule
from app.services.schedules import (
    ScheduleValidationError,
    calculate_next_run,
    parse_local_datetime,
    validate_schedule_values,
)


class ScheduleConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduleConfigurationResult:
    schedule: ProjectSchedule | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def _utc_value(value):
    """Return a canonical UTC datetime for idempotency comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_text(value, timezone_name):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = timezone.utc
    return value.astimezone(zone).strftime("%Y-%m-%dT%H:%M")


def _utc_minute_key(value):
    """Canonicalise persisted/requested schedule datetimes for comparison.

    SQLite drops timezone information from DateTime values even when
    timezone=True, while PostgreSQL preserves it. Journeyman stores schedule
    datetimes in UTC, so a naive value read from SQLite is UTC rather than
    local time. Comparing this primitive representation keeps idempotency
    independent of the database backend.
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def schedule_configuration_document(schedule):
    return {
        "id": schedule.id,
        "name": schedule.name,
        "project": schedule.project.name if schedule.project else "",
        "schedule_type": schedule.schedule_type,
        "timezone": schedule.timezone_name,
        "start_at": _local_text(schedule.start_at, schedule.timezone_name),
        "end_at": _local_text(schedule.end_at, schedule.timezone_name),
        "interval_minutes": schedule.interval_minutes,
        "weekdays": [
            int(value)
            for value in (schedule.weekdays or "").split(",")
            if value != ""
        ],
        "enabled": bool(schedule.enabled),
        "next_run_at": (
            schedule.next_run_at.isoformat()
            if schedule.next_run_at is not None
            else None
        ),
    }


def _normalise(values):
    if not isinstance(values, dict):
        raise ScheduleConfigurationError("Schedule configuration must be a mapping.")

    name = _clean(values.get("name"))
    project_name = _clean(values.get("project"))
    schedule_type = _clean(values.get("schedule_type")) or "once"
    timezone_name = _clean(values.get("timezone") or values.get("timezone_name")) or "UTC"
    start_text = _clean(values.get("start_at"))
    end_text = _clean(values.get("end_at"))

    if not name:
        raise ScheduleConfigurationError("Schedule name is required.")
    if not project_name:
        raise ScheduleConfigurationError("Project name is required.")
    if not start_text:
        raise ScheduleConfigurationError("Start date and time is required.")

    project = Project.query.filter_by(name=project_name).first()
    if project is None:
        raise ScheduleConfigurationError('Project "{}" does not exist.'.format(project_name))

    interval_minutes = values.get("interval_minutes")
    if interval_minutes in ("", None):
        interval_minutes = None
    else:
        try:
            interval_minutes = int(interval_minutes)
        except (TypeError, ValueError) as exc:
            raise ScheduleConfigurationError("Interval must be an integer number of minutes.") from exc

    weekdays = values.get("weekdays") or []
    if not isinstance(weekdays, (list, tuple, set)):
        raise ScheduleConfigurationError("weekdays must be a list of integers from 0 to 6.")
    try:
        weekday_set = {int(value) for value in weekdays}
    except (TypeError, ValueError) as exc:
        raise ScheduleConfigurationError("weekdays must contain integers from 0 to 6.") from exc

    try:
        start_at = parse_local_datetime(start_text, timezone_name)
        end_at = (
            parse_local_datetime(end_text, timezone_name)
            if schedule_type != "once" and end_text
            else None
        )
    except ScheduleValidationError as exc:
        raise ScheduleConfigurationError(str(exc)) from exc

    errors = validate_schedule_values(
        schedule_type,
        interval_minutes,
        weekday_set,
        start_at=start_at,
        end_at=end_at,
    )
    if errors:
        raise ScheduleConfigurationError(" ".join(errors))

    return {
        "name": name,
        "project": project,
        "project_name": project.name,
        "schedule_type": schedule_type,
        "timezone_name": timezone_name,
        "start_at": start_at,
        "end_at": end_at,
        "start_text": start_text,
        "end_text": end_text if schedule_type != "once" else "",
        "interval_minutes": interval_minutes if schedule_type == "interval" else None,
        "weekdays": sorted(weekday_set) if schedule_type == "weekly" else [],
        "enabled": bool(values.get("enabled", True)),
    }


def configure_schedule(values, *, created_by="system"):
    desired = _normalise(values)
    schedule = ProjectSchedule.query.filter_by(
        project_id=desired["project"].id,
        name=desired["name"],
    ).first()

    created = schedule is None
    if created:
        schedule = ProjectSchedule(
            project_id=desired["project"].id,
            name=desired["name"],
            created_by=_clean(created_by) or "system",
            start_at=desired["start_at"],
        )
        db.session.add(schedule)
        current = None
    else:
        current = {
            "project_id": schedule.project_id,
            "name": schedule.name,
            "schedule_type": schedule.schedule_type,
            "timezone_name": schedule.timezone_name,
            "start_at": _utc_minute_key(schedule.start_at),
            "end_at": _utc_minute_key(schedule.end_at),
            "interval_minutes": schedule.interval_minutes,
            "weekdays": schedule.weekdays or "",
            "enabled": bool(schedule.enabled),
        }

    comparable = {
        "project_id": desired["project"].id,
        "name": desired["name"],
        "schedule_type": desired["schedule_type"],
        "timezone_name": desired["timezone_name"],
        "start_at": _utc_minute_key(desired["start_at"]),
        "end_at": _utc_minute_key(desired["end_at"]),
        "interval_minutes": desired["interval_minutes"],
        "weekdays": ",".join(str(value) for value in desired["weekdays"]),
        "enabled": desired["enabled"],
    }
    changed = created or current != comparable
    if not changed:
        return ScheduleConfigurationResult(
            schedule,
            False,
            'Schedule "{}" is already configured.'.format(desired["name"]),
        )

    schedule.project_id = desired["project"].id
    schedule.name = desired["name"]
    schedule.schedule_type = desired["schedule_type"]
    schedule.timezone_name = desired["timezone_name"]
    schedule.start_at = desired["start_at"]
    schedule.end_at = desired["end_at"]
    schedule.interval_minutes = desired["interval_minutes"]
    schedule.weekdays = ",".join(str(value) for value in desired["weekdays"])
    schedule.enabled = desired["enabled"]
    schedule.claimed_at = None
    schedule.next_run_at = calculate_next_run(schedule) if schedule.enabled else None
    if schedule.enabled and schedule.next_run_at is None:
        schedule.enabled = False
        comparable["enabled"] = False

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ScheduleConfigurationError("Unable to save Schedule configuration.") from exc

    return ScheduleConfigurationResult(
        schedule,
        True,
        'Schedule "{}" {}.'.format(desired["name"], "created" if created else "updated"),
    )


def delete_schedule(project_name, name):
    project_name = _clean(project_name)
    name = _clean(name)
    if not project_name:
        raise ScheduleConfigurationError("Project name is required.")
    if not name:
        raise ScheduleConfigurationError("Schedule name is required.")

    project = Project.query.filter_by(name=project_name).first()
    if project is None:
        return ScheduleConfigurationResult(
            None,
            False,
            'Schedule "{}" is already absent.'.format(name),
        )

    schedule = ProjectSchedule.query.filter_by(project_id=project.id, name=name).first()
    if schedule is None:
        return ScheduleConfigurationResult(
            None,
            False,
            'Schedule "{}" is already absent.'.format(name),
        )

    db.session.delete(schedule)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ScheduleConfigurationError('Unable to delete Schedule "{}".'.format(name)) from exc

    return ScheduleConfigurationResult(None, True, 'Schedule "{}" deleted.'.format(name))
