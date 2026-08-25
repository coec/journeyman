from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from app import db
from app.models import Environment, Inventory, Job, Repository, Runner
from app.services.runners import runner_health
from app.services.environments import APPLICATION_ENVIRONMENT_NAME


STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


def _now():
    return datetime.now(timezone.utc)


def _check(name, status, summary, details=""):
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "details": details,
    }


def _service_status(unit_name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit_name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check(
            unit_name,
            STATUS_UNKNOWN,
            "Unable to query systemd.",
            str(exc),
        )

    state = (result.stdout or result.stderr or "unknown").strip()
    if result.returncode == 0 and state == "active":
        return _check(unit_name, STATUS_HEALTHY, "Service is active.", state)
    if state in {"inactive", "failed", "activating", "deactivating"}:
        return _check(unit_name, STATUS_FAILED, "Service is not active.", state)
    return _check(unit_name, STATUS_UNKNOWN, "Service state is unknown.", state)


def _format_bytes(value):
    value = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return "{:.1f} {}".format(value, unit)


def _mount_point(path):
    candidate = Path(path).resolve()
    while candidate.parent != candidate and not os.path.ismount(candidate):
        candidate = candidate.parent
    return str(candidate)


def storage_path_status(path_value, name):
    path = Path(path_value)
    try:
        usage = shutil.disk_usage(path)
        mount_point = _mount_point(path)
    except OSError as exc:
        return {
            "name": name,
            "path": str(path),
            "mount_point": "unknown",
            "status": STATUS_FAILED,
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "used_percent": 0.0,
            "total_display": "—",
            "used_display": "—",
            "free_display": "—",
            "error": str(exc),
        }

    used_percent = (usage.used / usage.total * 100) if usage.total else 0.0
    if used_percent >= 90:
        status = STATUS_FAILED
    elif used_percent >= 80:
        status = STATUS_WARNING
    else:
        status = STATUS_HEALTHY

    return {
        "name": name,
        "path": str(path),
        "mount_point": mount_point,
        "status": status,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": used_percent,
        "total_display": _format_bytes(usage.total),
        "used_display": _format_bytes(usage.used),
        "free_display": _format_bytes(usage.free),
        "error": "",
    }


def collect_storage_status():
    repository_root = Path(current_app.config["REPOSITORY_ROOT"])
    managed_environment_root = Path(
        current_app.config["MANAGED_ENVIRONMENT_ROOT"]
    )

    data_root = repository_root.parent
    application_root = managed_environment_root.parent

    return [
        storage_path_status(data_root, "Journeyman data"),
        storage_path_status(application_root, "Journeyman application"),
    ]


def _disk_status(path_value):
    path = Path(path_value)
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return _check(
            "Job workspace storage",
            STATUS_FAILED,
            "Storage path cannot be inspected.",
            "{}: {}".format(path, exc),
        )

    free_percent = (usage.free / usage.total * 100) if usage.total else 0
    free_gib = usage.free / (1024 ** 3)
    if free_percent < 5:
        status = STATUS_FAILED
    elif free_percent < 15:
        status = STATUS_WARNING
    else:
        status = STATUS_HEALTHY
    return _check(
        "Job workspace storage",
        status,
        "{:.1f} GiB free ({:.1f}%).".format(free_gib, free_percent),
        str(path),
    )


def _database_status():
    try:
        db.session.execute(text("SELECT 1"))
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    except Exception as exc:  # status page must remain renderable
        db.session.rollback()
        return _check("Database", STATUS_FAILED, "Database query failed.", str(exc))

    return _check(
        "Database",
        STATUS_HEALTHY,
        "Database is responding.",
        "Migration revision: {}".format(revision or "unknown"),
    )


def collect_system_status():
    from app.services.secret_lifecycle import collect_secret_lifecycle_checks

    storage = collect_storage_status()
    checks = [
        _database_status(),
        _service_status("journeyman-runner.service"),
        _service_status("journeyman-environment-builder.service"),
        _disk_status(current_app.config["LOG_ROOT"]),
    ]
    checks.extend(collect_secret_lifecycle_checks())

    job_counts = {
        status: Job.query.filter_by(status=status).count()
        for status in ("queued", "running", "failed", "cancelled")
    }
    repository_issues = Repository.query.filter(
        Repository.status.in_(["failed", "never_synced"])
    ).count()
    inventory_issues = Inventory.query.filter(
        Inventory.enabled.is_(True),
        Inventory.status.notin_(["ready", "synced", "successful"]),
    ).count()
    environment_issues = Environment.query.filter(
        Environment.enabled.is_(True),
        Environment.name != APPLICATION_ENVIRONMENT_NAME,
        Environment.validation_status.notin_(["passed", "valid", "successful"]),
    ).count()

    registered_runners = [
        {
            "name": runner.name,
            "site": runner.site,
            "hostname": runner.hostname,
            "health": runner_health(runner),
            "last_heartbeat_at": runner.last_heartbeat_at,
            "running_steps": runner.running_steps,
            "max_concurrent_steps": runner.max_concurrent_steps,
        }
        for runner in Runner.query.order_by(Runner.name.asc()).all()
    ]

    overall = STATUS_HEALTHY
    health_items = checks + storage
    if any(item["status"] == STATUS_FAILED for item in health_items):
        overall = STATUS_FAILED
    elif any(
        item["status"] in {STATUS_WARNING, STATUS_UNKNOWN}
        for item in health_items
    ):
        overall = STATUS_WARNING

    if any(item["health"] == "offline" for item in registered_runners):
        overall = STATUS_FAILED
    elif any(item["health"] in {"warning", "pending", "disabled"} for item in registered_runners) and overall == STATUS_HEALTHY:
        overall = STATUS_WARNING

    return {
        "checked_at": _now(),
        "overall": overall,
        "checks": checks,
        "storage": storage,
        "job_counts": job_counts,
        "repository_issues": repository_issues,
        "inventory_issues": inventory_issues,
        "environment_issues": environment_issues,
        "hostname": os.uname().nodename,
        "runners": registered_runners,
    }
