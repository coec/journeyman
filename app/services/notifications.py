"""Durable Journeyman notification events, rule resolution and delivery."""

import json
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.request import Request, urlopen

from flask import current_app
from sqlalchemy import event
from sqlalchemy.orm import object_session

from app import db
from app.models.job import Job, JobStep
from app.models.notification import (
    CHANNEL_EMAIL, CHANNEL_SYSLOG, CHANNEL_WEBHOOK,
    NotificationDelivery, NotificationEvent, NotificationRule, NotificationTarget,
)
from app.models.reaction import Reaction, Reactor
from app.models.project import Project
from app.models.project_step import ProjectStep
from app.models.project_package import ProjectPackage
from app.services.audit import record_audit_event
from app.services.outbound_security import (
    validate_http_header_value,
    validate_outbound_destination,
    validate_outbound_url,
)


JOB_STATUS_EVENTS = {
    "running": "execution.started",
    "successful": "execution.succeeded",
    "failed": "execution.failed",
    "cancelled": "execution.cancelled",
}
STEP_STATUS_EVENTS = {
    "running": "step.started",
    "successful": "step.succeeded",
    "failed": "step.failed",
    "cancelled": "step.cancelled",
}

PROJECT_EVENTS = (
    ("execution.started", "Execution started"),
    ("execution.succeeded", "Execution succeeded"),
    ("execution.failed", "Execution failed"),
    ("execution.cancelled", "Execution cancelled"),
    ("oversight.required", "Oversight required"),
)
STEP_EVENTS = (
    ("step.started", "Step started"),
    ("step.succeeded", "Step succeeded"),
    ("step.failed", "Step failed"),
    ("step.cancelled", "Step cancelled"),
)
PACKAGE_EVENTS = PROJECT_EVENTS
REACTOR_EVENTS = PROJECT_EVENTS[:4]


def utcnow():
    return datetime.now(timezone.utc)


def _delete_scope_rules(mapper, connection, target):
    scope_type = {
        Project: "project",
        ProjectStep: "project_step",
        ProjectPackage: "package",
        Reactor: "reactor",
    }.get(type(target))
    if scope_type and getattr(target, "id", None) is not None:
        connection.execute(
            NotificationRule.__table__.delete().where(
                NotificationRule.scope_type == scope_type,
                NotificationRule.scope_id == target.id,
            )
        )


for _scope_model in (Project, ProjectStep, ProjectPackage, Reactor):
    event.listen(_scope_model, "before_delete", _delete_scope_rules)


EVENT_MESSAGES = {
    "execution.started": "Project execution started.",
    "execution.succeeded": "Project execution completed successfully.",
    "execution.failed": "Project execution failed.",
    "execution.cancelled": "Project execution was cancelled.",
    "step.started": "Project step started.",
    "step.succeeded": "Project step completed successfully.",
    "step.failed": "Project step failed.",
    "step.cancelled": "Project step was cancelled.",
    "oversight.required": "Oversight is required before the next workflow step(s) can continue.",
}


def _event_snapshot(event_type, job, *, step=None, status=None, message=None):
    snapshot = {
        "project_name": str(job.project_name or ""),
        "requested_by": str(job.requested_by or ""),
        "status": str(status or job.status or ""),
        "message": str(message or EVENT_MESSAGES.get(event_type, "")),
        "oversight_reviewer": str(job.oversight_reviewer or job.requested_by or ""),
    }
    if job.package_snapshot:
        snapshot["package_name"] = str(job.package_snapshot.package_name or "")
    if step is not None:
        snapshot.update({
            "step_position": step.position,
            "step_name": str(step.name or step.playbook or ""),
        })
    return snapshot


def queue_notification_event(
    event_type, *, job=None, step=None, reaction=None, event_key="",
    status=None, message=None, snapshot_extra=None,
):
    """Queue one canonical event with an immutable display snapshot."""
    session = object_session(job or step or reaction)
    if session is None or job is None or getattr(job, "id", None) is None:
        return None
    # Status attribute listeners can fire while a Job/JobStep graph is still
    # being assembled.  Reading lazy relationships while those related objects
    # are not yet in the Session would otherwise trigger an autoflush and emit
    # SQLAlchemy warnings such as "Object of type <JobStep> not in session".
    # The notification snapshot is read-only, so suppressing autoflush for this
    # lookup is both sufficient and preserves normal relationship loading.
    with session.no_autoflush:
        snapshot = _event_snapshot(
            str(event_type),
            job,
            step=step,
            status=status,
            message=message,
        )
    if snapshot_extra:
        snapshot.update(snapshot_extra)

    canonical_type = str(event_type)
    canonical_key = str(event_key or "")[:255]
    step_id = getattr(step, "id", None) if step is not None else None
    reaction_id = getattr(reaction, "id", None) if reaction is not None else None

    # Status listeners can observe the same logical transition more than once
    # while sliced execution and post-step refresh/replanning update the parent
    # JobStep.  The database uniqueness constraint defines these events as
    # canonical, so make enqueueing idempotent instead of relying on callers to
    # suppress every internal duplicate transition.  Check pending rows first
    # because they may not have been flushed yet.
    for pending in session.new:
        if not isinstance(pending, NotificationEvent):
            continue
        if (
            pending.event_type == canonical_type
            and pending.event_key == canonical_key
            and pending.job_id == job.id
            and pending.step_id == step_id
            and pending.reaction_id == reaction_id
        ):
            return pending

    with session.no_autoflush:
        existing = (
            session.query(NotificationEvent)
            .filter_by(
                event_type=canonical_type,
                event_key=canonical_key,
                job_id=job.id,
                step_id=step_id,
                reaction_id=reaction_id,
            )
            .first()
        )
    if existing is not None:
        return existing

    row = NotificationEvent(
        event_type=canonical_type,
        event_key=canonical_key,
        job_id=job.id,
        step_id=step_id,
        reaction_id=reaction_id,
        snapshot_json=json.dumps(snapshot, sort_keys=True),
    )
    session.add(row)
    return row


@event.listens_for(Job.status, "set", active_history=True)
def _queue_job_status_event(job, value, oldvalue, initiator):
    if value == oldvalue:
        return
    event_type = JOB_STATUS_EVENTS.get(str(value or "").strip().lower())
    if not event_type:
        return

    # A Job may move back to queued/running after an Oversight boundary.  That
    # is continuation of the same execution, not a second execution start.
    # started_at is already populated once the initial execution has begun.
    if event_type == "execution.started" and job.started_at is not None:
        return

    queue_notification_event(
        event_type,
        job=job,
        event_key=str(value),
        status=str(value),
    )


@event.listens_for(JobStep.status, "set", active_history=True)
def _queue_step_status_event(step, value, oldvalue, initiator):
    if value == oldvalue:
        return
    event_type = STEP_STATUS_EVENTS.get(str(value or "").strip().lower())

    # A sliced step can temporarily move from successful back to running while
    # its post-step inventory refresh/replanning is completed. That internal
    # transition is not a second logical step start. started_at is populated
    # after the initial pending -> running transition and remains set for the
    # lifetime of the step.
    if event_type == "step.started" and step.started_at is not None:
        return

    if event_type and step.job is not None:
        queue_notification_event(
            event_type,
            job=step.job,
            step=step,
            event_key=str(value),
            status=str(value),
        )


def queue_oversight_required(job, steps):
    positions = ",".join(str(step.position) for step in steps)
    return queue_notification_event(
        "oversight.required",
        job=job,
        event_key="steps:{}".format(positions),
        status="waiting_oversight",
        snapshot_extra={
            "oversight_step_positions": [step.position for step in steps],
        },
    )


def _linked_reaction(job):
    return Reaction.query.filter_by(job_id=job.id).one_or_none()


def _matching_target_ids(event_row):
    job = event_row.job
    if job is None:
        return set()

    scopes = [("project", job.project_id)]
    if job.package_snapshot and job.package_snapshot.package_id:
        scopes.append(("package", job.package_snapshot.package_id))
    if event_row.step and event_row.step.project_step_id:
        scopes.append(("project_step", event_row.step.project_step_id))
    reaction = event_row.reaction or _linked_reaction(job)
    if reaction and reaction.reactor_id:
        scopes.append(("reactor", reaction.reactor_id))

    target_ids = set()
    for scope_type, scope_id in scopes:
        for row in NotificationRule.query.filter_by(
            scope_type=scope_type,
            scope_id=scope_id,
            event_type=event_row.event_type,
        ).all():
            if row.target and row.target.enabled:
                target_ids.add(row.target_id)
    return target_ids


def _public_job_url(job):
    fqdn = str(current_app.config.get("PUBLIC_FQDN") or "").strip()
    port = int(current_app.config.get("HTTPS_PORT", 443))
    suffix = "" if port == 443 else ":{}".format(port)
    return "https://{}{}{}".format(fqdn, suffix, "/jobs/{}".format(job.id))


def _snapshot_for(event_row):
    try:
        value = json.loads(event_row.snapshot_json or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _message_for(event_row):
    job = event_row.job
    step = event_row.step
    snapshot = _snapshot_for(event_row)
    event_label = event_row.event_type.replace(".", " ").title()
    project_name = snapshot.get("project_name") or job.project_name
    requested_by = snapshot.get("requested_by") or job.requested_by
    status = snapshot.get("status") or job.status
    subject = "Journeyman: {} — {}".format(event_label, project_name)
    lines = [
        "Journeyman notification",
        "",
        "Event: {}".format(event_row.event_type),
        "Project: {}".format(project_name),
        "Job: #{}".format(job.id),
        "Requested by: {}".format(requested_by),
        "Status: {}".format(status),
    ]
    package_name = snapshot.get("package_name")
    if package_name:
        lines.append("Package: {}".format(package_name))
    elif not snapshot and job.package_snapshot:
        lines.append("Package: {}".format(job.package_snapshot.package_name))
    if step is not None:
        step_position = snapshot.get("step_position", step.position)
        step_name = snapshot.get("step_name") or step.name or step.playbook
        lines.append("Step: {} — {}".format(step_position, step_name))
    if event_row.event_type == "oversight.required":
        reviewer = snapshot.get("oversight_reviewer") or job.oversight_reviewer or requested_by
        lines.append("Reviewer: {}".format(reviewer))
        lines.append("Review: {}/oversight".format(_public_job_url(job)))
    else:
        lines.append("Job details: {}".format(_public_job_url(job)))
    message = snapshot.get("message")
    if not message and not snapshot:
        message = job.message
    if message:
        lines.extend(["", "Message: {}".format(message)])
    return subject, "\n".join(lines)


def _send_email(target, subject, body):
    validate_outbound_destination(
        target.host,
        target.port or 25,
        purpose="Notification SMTP",
        allow_self=True,
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = target.sender
    recipients = [item.strip() for item in target.recipients.replace(";", ",").split(",") if item.strip()]
    if not recipients:
        raise ValueError("Email Notification Target has no recipients.")
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    tls_mode = str(target.tls_mode or "starttls").lower()
    timeout = 10
    if tls_mode == "ssl":
        client = smtplib.SMTP_SSL(target.host, target.port or 465, timeout=timeout, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(target.host, target.port or 25, timeout=timeout)
    try:
        if tls_mode == "starttls":
            client.starttls(context=ssl.create_default_context())
        if target.username:
            client.login(target.username, target.get_secret())
        client.send_message(msg, to_addrs=recipients)
    finally:
        try:
            client.quit()
        except Exception:
            pass


def _send_webhook(target, subject, body, event_row):
    url = validate_outbound_url(target.url, purpose="Notification webhook", require_https=True)
    payload = json.dumps({
        "event": event_row.event_type,
        "subject": subject,
        "message": body,
        "job_id": event_row.job_id,
        "step_id": event_row.step_id,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "Journeyman-Notifications/1"}
    secret = target.get_secret()
    if secret:
        headers["Authorization"] = validate_http_header_value(
            "Bearer {}".format(secret), purpose="Notification Authorization header"
        )
    request = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        if not (200 <= response.status < 300):
            raise RuntimeError("Webhook returned HTTP {}.".format(response.status))


def _send_syslog(target, subject, body):
    port = target.port or 514
    host = validate_outbound_destination(target.host, port, purpose="Notification syslog")
    message = "<134>{} {}".format(subject, body.replace("\n", " | ")).encode("utf-8", errors="replace")
    sock_type = socket.SOCK_STREAM if target.syslog_protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.settimeout(10)
        if sock_type == socket.SOCK_STREAM:
            sock.connect((host, port))
            sock.sendall(message + b"\n")
        else:
            sock.sendto(message, (host, port))


def test_notification_target(target):
    """Synchronously send one explicit administrator-requested test."""

    subject = "Journeyman: Test notification — {}".format(target.name)
    body = "\n".join((
        "Journeyman notification test",
        "",
        "Target: {}".format(target.name),
        "Channel: {}".format(target.channel.title()),
        "",
        "This is a test notification generated by Journeyman.",
    ))

    if target.channel == CHANNEL_EMAIL:
        _send_email(target, subject, body)
        return
    if target.channel == CHANNEL_SYSLOG:
        _send_syslog(target, subject, body)
        return
    if target.channel == CHANNEL_WEBHOOK:
        url = validate_outbound_url(
            target.url,
            purpose="Notification webhook",
            require_https=True,
        )
        payload = json.dumps({
            "event": "notification.test",
            "subject": subject,
            "message": body,
            "target_id": target.id,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Journeyman-Notifications/1",
        }
        secret = target.get_secret()
        if secret:
            headers["Authorization"] = validate_http_header_value(
            "Bearer {}".format(secret), purpose="Notification Authorization header"
        )
        request = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=10) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(
                    "Webhook returned HTTP {}.".format(response.status)
                )
        return
    raise ValueError(
        "Unsupported Notification Target channel: {}".format(target.channel)
    )


def _deliver(row):
    target = row.target
    event_row = row.event
    subject, body = _message_for(event_row)
    if target.channel == CHANNEL_EMAIL:
        _send_email(target, subject, body)
    elif target.channel == CHANNEL_WEBHOOK:
        _send_webhook(target, subject, body, event_row)
    elif target.channel == CHANNEL_SYSLOG:
        _send_syslog(target, subject, body)
    else:
        raise ValueError("Unsupported Notification Target channel: {}".format(target.channel))


def process_pending_notifications(limit=50):
    """Resolve new events, de-duplicate targets, and attempt queued deliveries."""
    events = (
        NotificationEvent.query
        .filter(NotificationEvent.processed_at.is_(None))
        .order_by(NotificationEvent.id.asc())
        .limit(limit)
        .all()
    )
    for event_row in events:
        for target_id in sorted(_matching_target_ids(event_row)):
            exists = NotificationDelivery.query.filter_by(event_id=event_row.id, target_id=target_id).first()
            if not exists:
                db.session.add(NotificationDelivery(event_id=event_row.id, target_id=target_id))
        event_row.processed_at = utcnow()
    db.session.commit()

    deliveries = (
        NotificationDelivery.query
        .filter(NotificationDelivery.status.in_(["pending", "failed"]))
        .filter(NotificationDelivery.attempts < 3)
        .order_by(NotificationDelivery.id.asc())
        .limit(limit)
        .all()
    )
    sent = failed = 0
    for row in deliveries:
        row.attempts += 1
        try:
            _deliver(row)
            row.status = "sent"
            row.sent_at = utcnow()
            row.last_error = ""
            sent += 1
            db.session.commit()
            record_audit_event(
                "notification.sent",
                object_type="notification_target",
                object_id=row.target_id,
                object_name=row.target.name,
                actor_username="system",
                details={"event": row.event.event_type, "job_id": row.event.job_id, "delivery_id": row.id},
            )
        except Exception as exc:
            row.status = "failed"
            row.last_error = str(exc)[:4000]
            failed += 1
            db.session.commit()
            record_audit_event(
                "notification.failed",
                result="failure",
                object_type="notification_target",
                object_id=row.target_id,
                object_name=row.target.name,
                actor_username="system",
                details={"event": row.event.event_type, "job_id": row.event.job_id, "delivery_id": row.id, "error": str(exc)},
            )
    return {"events": len(events), "sent": sent, "failed": failed}
