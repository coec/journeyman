"""Retention and purge controls for protected Journeyman operational data."""

from datetime import datetime, timedelta, timezone

from flask import current_app

from app import db
from app.models import Job, Reaction, SystemSetting
from app.models.system_setting import SYSTEM_SETTING_ID
from app.services.audit import record_audit_event
from app.services.configuration_deletion import cleanup_job_output
from app.services.inventory_cache import purge_expired_inventory_caches


TERMINAL_JOB_STATUSES = {"successful", "failed", "cancelled"}
TERMINAL_REACTION_STATUSES = {
    "observed",
    "successful",
    "failed",
    "cancelled",
    "suppressed",
}
DEFAULT_RETENTION_DAYS = 180
MAX_RETENTION_DAYS = 36500


class DataRetentionValidationError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__(" ".join(self.errors))


def _utcnow():
    return datetime.now(timezone.utc)


def _configured_retention_days(attribute, config_key):
    settings = db.session.get(SystemSetting, SYSTEM_SETTING_ID)
    if settings is not None:
        value = getattr(settings, attribute, None)
        if value is not None:
            return int(value)
    return int(current_app.config.get(config_key, DEFAULT_RETENTION_DAYS))


def retention_settings_form_data(form):
    return {
        "job_retention_days": str(form.get("job_retention_days") or "").strip(),
        "reaction_retention_days": str(form.get("reaction_retention_days") or "").strip(),
    }


def retention_settings_to_form_data(settings):
    return {
        "job_retention_days": str(settings.job_retention_days),
        "reaction_retention_days": str(settings.reaction_retention_days),
    }


def validate_retention_settings(values):
    errors = []
    normalized = {}
    for key, label in (
        ("job_retention_days", "Job retention days"),
        ("reaction_retention_days", "Reaction retention days"),
    ):
        raw = str(values.get(key) or "").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors.append("{} must be a whole number.".format(label))
            continue
        if not 0 <= value <= MAX_RETENTION_DAYS:
            errors.append(
                "{} must be between 0 and {}.".format(label, MAX_RETENTION_DAYS)
            )
            continue
        normalized[key] = value
    if errors:
        raise DataRetentionValidationError(errors)
    return normalized


def update_retention_settings(settings, values, *, updated_by):
    settings.job_retention_days = values["job_retention_days"]
    settings.reaction_retention_days = values["reaction_retention_days"]
    settings.updated_by = str(updated_by or "").strip() or "system"
    db.session.commit()
    return settings


def purge_expired_jobs(*, now=None, dry_run=False):
    """Delete completed Jobs older than the configured retention period."""

    retention_days = _configured_retention_days(
        "job_retention_days", "JOB_RETENTION_DAYS"
    )
    if retention_days <= 0:
        return []

    now = now or _utcnow()
    cutoff = now - timedelta(days=retention_days)

    jobs = (
        Job.query
        .filter(Job.status.in_(TERMINAL_JOB_STATUSES))
        .filter(Job.finished_at.is_not(None))
        .filter(Job.finished_at < cutoff)
        .order_by(Job.id)
        .all()
    )
    job_ids = [job.id for job in jobs]

    if dry_run or not jobs:
        return job_ids

    for job in jobs:
        db.session.delete(job)
    db.session.commit()

    cleanup_errors = cleanup_job_output(job_ids)
    record_audit_event(
        "data_retention.jobs_purged",
        authenticated_via="scheduler",
        details={
            "job_count": len(job_ids),
            "retention_days": retention_days,
            "oldest_cutoff": cutoff.isoformat(),
            "cleanup_errors": cleanup_errors,
        },
    )
    return job_ids


def purge_expired_reactions(*, now=None, dry_run=False):
    """Delete terminal Reaction history older than the configured period."""

    retention_days = _configured_retention_days(
        "reaction_retention_days", "REACTION_RETENTION_DAYS"
    )
    if retention_days <= 0:
        return []

    now = now or _utcnow()
    cutoff = now - timedelta(days=retention_days)
    reactions = (
        Reaction.query
        .filter(Reaction.status.in_(TERMINAL_REACTION_STATUSES))
        .filter(Reaction.created_at < cutoff)
        .order_by(Reaction.id)
        .all()
    )
    reaction_ids = [reaction.id for reaction in reactions]

    if dry_run or not reactions:
        return reaction_ids

    for reaction in reactions:
        db.session.delete(reaction)
    db.session.commit()

    record_audit_event(
        "data_retention.reactions_purged",
        authenticated_via="scheduler",
        details={
            "reaction_count": len(reaction_ids),
            "retention_days": retention_days,
            "oldest_cutoff": cutoff.isoformat(),
        },
    )
    return reaction_ids


def purge_expired_protected_data(*, now=None, dry_run=False):
    """Apply configured Job, Reaction and inventory-cache retention policies."""

    now = now or _utcnow()
    reaction_ids = purge_expired_reactions(now=now, dry_run=dry_run)
    job_ids = purge_expired_jobs(now=now, dry_run=dry_run)
    cache_paths = purge_expired_inventory_caches(
        max_age_seconds=int(
            current_app.config.get("INVENTORY_CACHE_RETENTION_SECONDS", 604800)
        ),
        now=now,
        dry_run=dry_run,
    )
    return {
        "job_ids": job_ids,
        "reaction_ids": reaction_ids,
        "inventory_cache_paths": cache_paths,
    }
