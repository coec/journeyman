"""Notification Target and notification-rule administration."""

from flask import current_app
from sqlalchemy import and_, or_

from app.models import NotificationRule, NotificationTarget, Project, ProjectPackage, ProjectStep, Reactor
from app.models.notification import CHANNEL_EMAIL, CHANNEL_SYSLOG, CHANNEL_WEBHOOK, VALID_NOTIFICATION_CHANNELS
from app.services.notifications import (
    PACKAGE_EVENTS, PROJECT_EVENTS, REACTOR_EVENTS, STEP_EVENTS,
    test_notification_target,
)
from app.services.outbound_security import OutboundSecurityError, validate_outbound_destination, validate_outbound_url
from app.routes import (
    abort, bp, current_user_is_admin, db, flash, record_audit_event,
    redirect, render_template, request, url_for,
)


def _admin_only():
    if not current_user_is_admin():
        abort(403)


def _clean(value):
    return str(value or "").strip()


def _target_form(target=None):
    if request.method == "POST":
        try:
            port = int(request.form.get("port") or 0)
        except (TypeError, ValueError):
            port = -1
        return {
            "name": _clean(request.form.get("name")),
            "description": _clean(request.form.get("description")),
            "channel": _clean(request.form.get("channel")).lower(),
            "enabled": request.form.get("enabled") == "on",
            "host": _clean(request.form.get("host")),
            "port": port,
            "tls_mode": _clean(request.form.get("tls_mode")) or "starttls",
            "username": _clean(request.form.get("username")),
            "sender": _clean(request.form.get("sender")),
            "recipients": _clean(request.form.get("recipients")),
            "url": _clean(request.form.get("url")),
            "syslog_protocol": _clean(request.form.get("syslog_protocol")) or "udp",
            "secret": str(request.form.get("secret") or ""),
        }
    if target is None:
        return {
            "name": "", "description": "", "channel": CHANNEL_EMAIL, "enabled": True,
            "host": "", "port": 0, "tls_mode": "starttls", "username": "",
            "sender": "", "recipients": "", "url": "", "syslog_protocol": "udp", "secret": "",
        }
    return {
        "name": target.name, "description": target.description, "channel": target.channel,
        "enabled": target.enabled, "host": target.host, "port": target.port,
        "tls_mode": target.tls_mode, "username": target.username, "sender": target.sender,
        "recipients": target.recipients, "url": target.url,
        "syslog_protocol": target.syslog_protocol, "secret": "",
    }


def _validate_target(data, target=None):
    errors = []
    if not data["name"]:
        errors.append("Name is required.")
    duplicate = NotificationTarget.query.filter_by(name=data["name"]).first()
    if duplicate is not None and (target is None or duplicate.id != target.id):
        errors.append("A Notification Target with this name already exists.")
    if data["channel"] not in VALID_NOTIFICATION_CHANNELS:
        errors.append("Select a valid notification channel.")
        return errors
    try:
        if data["channel"] == CHANNEL_EMAIL:
            if not data["host"] or not data["sender"] or not data["recipients"]:
                errors.append("Email targets require SMTP host, sender and recipients.")
            if data["port"] < 0 or data["port"] > 65535:
                errors.append("SMTP port is invalid.")
            if data["tls_mode"] not in {"none", "starttls", "ssl"}:
                errors.append("Select a valid SMTP TLS mode.")
            if data["host"]:
                validate_outbound_destination(
                    data["host"],
                    data["port"] or 25,
                    purpose="Notification SMTP",
                    allow_self=True,
                )
        elif data["channel"] == CHANNEL_WEBHOOK:
            if not data["url"]:
                errors.append("Webhook URL is required.")
            else:
                validate_outbound_url(data["url"], purpose="Notification webhook", require_https=True)
        else:
            if not data["host"]:
                errors.append("Syslog host is required.")
            if data["syslog_protocol"] not in {"udp", "tcp"}:
                errors.append("Select UDP or TCP for syslog.")
            if data["port"] < 0 or data["port"] > 65535:
                errors.append("Syslog port is invalid.")
            if data["host"]:
                validate_outbound_destination(data["host"], data["port"] or 514, purpose="Notification syslog")
    except OutboundSecurityError as exc:
        errors.append(str(exc))
    return errors


@bp.get("/notification-targets")
def notification_targets():
    _admin_only()
    targets = NotificationTarget.query.order_by(NotificationTarget.name.asc()).all()
    return render_template("notification_targets.html", targets=targets)


@bp.route("/notification-targets/new", methods=["GET", "POST"])
def notification_target_new():
    _admin_only()
    data = _target_form()
    if request.method == "POST":
        errors = _validate_target(data)
        if not errors:
            target = NotificationTarget()
            _apply_target(target, data, is_new=True)
            db.session.add(target)
            db.session.commit()
            record_audit_event("notification_target.create", object_type="notification_target", object_id=target.id, object_name=target.name, details={"channel": target.channel})
            flash('Notification Target "{}" created.'.format(target.name), "success")
            return redirect(url_for("main.notification_targets"))
        for error in errors:
            flash(error, "error")
    return render_template("notification_target_form.html", target=None, form_data=data)


@bp.route("/notification-targets/<int:target_id>/edit", methods=["GET", "POST"])
def notification_target_edit(target_id):
    _admin_only()
    target = db.get_or_404(NotificationTarget, target_id)
    data = _target_form(target)
    if request.method == "POST":
        errors = _validate_target(data, target)
        if not errors:
            _apply_target(target, data, is_new=False)
            db.session.commit()
            record_audit_event("notification_target.update", object_type="notification_target", object_id=target.id, object_name=target.name, details={"channel": target.channel})
            flash('Notification Target "{}" updated.'.format(target.name), "success")
            return redirect(url_for("main.notification_targets"))
        for error in errors:
            flash(error, "error")
    return render_template("notification_target_form.html", target=target, form_data=data)


def _apply_target(target, data, *, is_new):
    previous_channel = str(getattr(target, "channel", "") or "")
    for key in ("name", "description", "channel", "host", "port", "tls_mode", "username", "sender", "recipients", "url", "syslog_protocol", "enabled"):
        setattr(target, key, data[key])
    if data["secret"]:
        target.set_secret(data["secret"])
    elif is_new or (previous_channel and previous_channel != data["channel"]):
        # Never reinterpret an SMTP password as a webhook bearer token (or vice versa).
        target.set_secret("")


@bp.post("/notification-targets/<int:target_id>/test")
def notification_target_test(target_id):
    _admin_only()
    target = db.get_or_404(NotificationTarget, target_id)
    try:
        test_notification_target(target)
    except Exception as exc:
        record_audit_event(
            "notification.test_failed",
            result="failure",
            object_type="notification_target",
            object_id=target.id,
            object_name=target.name,
            details={"channel": target.channel, "error": str(exc)},
        )
        flash(
            'Test notification for "{}" failed: {}'.format(target.name, exc),
            "error",
        )
    else:
        record_audit_event(
            "notification.test_sent",
            object_type="notification_target",
            object_id=target.id,
            object_name=target.name,
            details={"channel": target.channel},
        )
        flash('Test notification sent to "{}".'.format(target.name), "success")
    return redirect(url_for("main.notification_targets"))


@bp.post("/notification-targets/<int:target_id>/delete")
def notification_target_delete(target_id):
    _admin_only()
    target = db.get_or_404(NotificationTarget, target_id)
    if target.rules or target.deliveries:
        flash(
            'Notification Target "{}" has notification rules or delivery history. Disable it instead of deleting it.'.format(target.name),
            "error",
        )
        return redirect(url_for("main.notification_targets"))
    name = target.name
    db.session.delete(target)
    db.session.commit()
    record_audit_event("notification_target.delete", object_type="notification_target", object_id=target_id, object_name=name)
    flash('Notification Target "{}" deleted.'.format(name), "success")
    return redirect(url_for("main.notification_targets"))


def _rules_page(scope_type, scope_id, title, back_url, allowed_scopes):
    _admin_only()
    targets = NotificationTarget.query.filter_by(enabled=True).order_by(NotificationTarget.name.asc()).all()
    rules = NotificationRule.query.filter(
        or_(*[
            and_(NotificationRule.scope_type == stype, NotificationRule.scope_id == sid)
            for stype, sid, _label, _events in allowed_scopes
        ])
    ).order_by(NotificationRule.scope_type.asc(), NotificationRule.scope_id.asc(), NotificationRule.event_type.asc()).all()
    scope_rows = [
        {"key": "{}:{}".format(stype, sid), "label": label, "events": list(events)}
        for stype, sid, label, events in allowed_scopes
    ]
    scope_label_by_key = {
        "{}:{}".format(stype, sid): label
        for stype, sid, label, _events in allowed_scopes
    }
    rule_scope_labels = {
        rule.id: scope_label_by_key.get(
            "{}:{}".format(rule.scope_type, rule.scope_id),
            rule.scope_type.replace("_", " ").title(),
        )
        for rule in rules
    }
    return render_template(
        "notification_rules.html", title=title, back_url=back_url,
        rules=rules, targets=targets, scope_rows=scope_rows,
        rule_scope_labels=rule_scope_labels,
        event_labels=dict(PROJECT_EVENTS + STEP_EVENTS),
    )


def _save_rule(allowed):
    scope_key = _clean(request.form.get("scope"))
    event_type = _clean(request.form.get("event_type"))
    try:
        target_id = int(request.form.get("target_id") or 0)
    except (TypeError, ValueError):
        target_id = 0
    allowed_map = {
        "{}:{}".format(st, sid): (
            st,
            sid,
            {
                event[0] if isinstance(event, (tuple, list)) else event
                for event in events
            },
        )
        for st, sid, _label, events in allowed
    }
    selected = allowed_map.get(scope_key)
    target = db.session.get(NotificationTarget, target_id)
    if selected is None or target is None or event_type not in selected[2]:
        flash("Invalid notification rule.", "error")
        return False
    stype, sid, _events = selected
    existing = NotificationRule.query.filter_by(scope_type=stype, scope_id=sid, event_type=event_type, target_id=target_id).first()
    if existing is None:
        db.session.add(NotificationRule(scope_type=stype, scope_id=sid, event_type=event_type, target_id=target_id))
        db.session.commit()
    return True


@bp.route("/projects/<int:project_id>/notifications", methods=["GET", "POST"])
def project_notifications(project_id):
    project = db.get_or_404(Project, project_id)
    allowed = [("project", project.id, "Project — {}".format(project.name), PROJECT_EVENTS)] + [
        ("project_step", step.id, "Step {} — {}".format(step.position, step.name or step.playbook), STEP_EVENTS)
        for step in project.steps
    ]
    if request.method == "POST":
        _admin_only()
        if _save_rule(allowed):
            flash("Notification rule added.", "success")
        return redirect(url_for("main.project_notifications", project_id=project.id))
    return _rules_page("project", project.id, "Project Notifications — {}".format(project.name), url_for("main.project_edit", project_id=project.id), allowed)


@bp.route("/packages/<int:package_id>/notifications", methods=["GET", "POST"])
def package_notifications(package_id):
    package = db.get_or_404(ProjectPackage, package_id)
    allowed = [("package", package.id, "Package — {}".format(package.name), PACKAGE_EVENTS)]
    if request.method == "POST":
        _admin_only(); _save_rule(allowed); flash("Notification rule added.", "success")
        return redirect(url_for("main.package_notifications", package_id=package.id))
    return _rules_page("package", package.id, "Package Notifications — {}".format(package.name), url_for("main.project_package_edit", package_id=package.id), allowed)


@bp.route("/reactors/<int:reactor_id>/notifications", methods=["GET", "POST"])
def reactor_notifications(reactor_id):
    reactor = db.get_or_404(Reactor, reactor_id)
    allowed = [("reactor", reactor.id, "Reactor — {}".format(reactor.name), REACTOR_EVENTS)]
    if request.method == "POST":
        _admin_only(); _save_rule(allowed); flash("Notification rule added.", "success")
        return redirect(url_for("main.reactor_notifications", reactor_id=reactor.id))
    return _rules_page("reactor", reactor.id, "Reactor Notifications — {}".format(reactor.name), url_for("main.reactor_edit", reactor_id=reactor.id), allowed)


@bp.post("/notification-rules/<int:rule_id>/delete")
def notification_rule_delete(rule_id):
    _admin_only()
    rule = db.get_or_404(NotificationRule, rule_id)
    return_to = _clean(request.form.get("return_to")) or url_for("main.notification_targets")
    db.session.delete(rule)
    db.session.commit()
    record_audit_event("notification_rule.delete", object_type="notification_rule", object_id=rule_id)
    flash("Notification rule removed.", "success")
    return redirect(return_to)
