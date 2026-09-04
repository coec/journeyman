"""Append immutable audit events for terminal Job outcomes."""

import json

from sqlalchemy import event
from sqlalchemy.orm import object_session

from app.models.audit_log import AuditLog
from app.models.job import Job


TERMINAL_AUDIT_RESULTS = {
    "successful": "success",
    "failed": "failure",
    "cancelled": "cancelled",
}


def _management_action(package_snapshot):
    """Return the non-secret Manage Remote Runner action, when present."""

    if package_snapshot is None:
        return ""

    try:
        display_values = package_snapshot.get_display_values()
    except (TypeError, ValueError):
        return ""

    for item in display_values:
        if not isinstance(item, dict):
            continue
        if item.get("variable_name") != "journeyman_manage_action":
            continue
        return str(item.get("value") or "").strip().lower()[:32]
    return ""


def queue_terminal_job_audit_event(job, status):
    """Add one terminal Job audit event to the Job's current transaction."""

    session = object_session(job)
    job_id = getattr(job, "id", None)
    canonical_status = str(status or "").strip().lower()
    result = TERMINAL_AUDIT_RESULTS.get(canonical_status)
    if session is None or job_id is None or result is None:
        return None

    action = "job.completed"
    object_name = str(job.project_name or "")
    details = {
        "job_id": job_id,
        "job_status": canonical_status,
        "project_id": job.project_id,
        "project_name": str(job.project_name or ""),
        "requested_by": str(job.requested_by or ""),
    }

    # Package snapshots contain only values classified as safe for display.
    # Reading them here avoids decrypting the full Package execution variables
    # merely to enrich an audit record.
    with session.no_autoflush:
        package_snapshot = job.package_snapshot
        if package_snapshot is not None:
            action = "package.execute.completed"
            object_name = str(package_snapshot.package_name or object_name)
            try:
                operational_targets = package_snapshot.get_operational_targets()
            except (TypeError, ValueError):
                operational_targets = []
            details.update(
                {
                    "package_id": package_snapshot.package_id,
                    "package_name": str(package_snapshot.package_name or ""),
                    "operational_targets": operational_targets,
                }
            )
            management_action = _management_action(package_snapshot)
            if management_action:
                details["management_action"] = management_action

    object_id = str(job_id)

    # A Job should enter a terminal state once. Be defensive about repeated
    # assignments from retry/recovery paths without weakening append-only audit
    # history. If the transition changes again before the transaction commits,
    # keep the pending row aligned with the state that will actually be saved.
    for pending in session.new:
        if not isinstance(pending, AuditLog):
            continue
        if (
            pending.action == action
            and pending.object_type == "job"
            and pending.object_id == object_id
        ):
            pending.result = result
            pending.object_name = object_name[:255]
            pending.details_json = json.dumps(details, sort_keys=True)
            return pending

    row = AuditLog(
        actor_username="system",
        actor_role="",
        authenticated_via="job-lifecycle",
        action=action,
        object_type="job",
        object_id=object_id,
        object_name=object_name[:255],
        result=result,
        source_ip="",
        request_id="",
        details_json=json.dumps(details, sort_keys=True),
    )
    session.add(row)
    return row


@event.listens_for(Job.status, "set", active_history=True)
def _audit_job_terminal_status(job, value, oldvalue, initiator):
    if value == oldvalue:
        return
    queue_terminal_job_audit_event(job, value)
    if str(value or "").strip().lower() in TERMINAL_AUDIT_RESULTS:
        # A management drain is owned by the Job, not by the runner heartbeat.
        # Releasing it here covers success, failure, cancellation and recovery.
        from app.services.runner_draining import release_runner_drain_for_job
        release_runner_drain_for_job(job)
