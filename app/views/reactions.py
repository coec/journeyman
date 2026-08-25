"""Journeyman Sources, Signals, Reactors and Reactions."""

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Response, abort, current_app, jsonify, render_template, request, redirect, stream_with_context, url_for, flash
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app import csrf, db
from app.auth import current_user_is_admin, current_username
from app.models import ProjectPackage, Reaction, Reactor, Runner, Signal, SignalSource
from app.models.reaction import (
    REACTOR_AUTOMATIC,
    REACTOR_OBSERVE,
    SOURCE_SNMP_TRAP,
    SOURCE_SYSLOG,
    SOURCE_ZABBIX,
)
from app.routes import bp
from app.services.audit import record_audit_event
from app.services.pagination import paginate_list, page_size_for_user
from app.services.reactor_configuration import (
    ReactorConfigurationError,
    delete_reactor,
)
from app.services.reactions import (
    ReactionError,
    process_signal,
    reactor_matches,
    resolve_reaction_inputs,
    sender_allowed,
    validate_allowed_networks,
    validate_match_definition,
    validate_mappings,
)
from app.services.runners import authenticate_runner
from app.services.runner_capabilities import runner_capability_rows


MAX_SIGNAL_BYTES = 256 * 1024
ZABBIX_TIMESTAMP_WINDOW_SECONDS = 300


def _admin_required():
    if not current_user_is_admin():
        abort(403)


def _parse_signal_time(value):
    value = str(value or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReactionError("Signal timestamp must be ISO-8601.") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _source_form_data(source=None):
    if request.method == "POST":
        return {
            "name": str(request.form.get("name") or "").strip(),
            "description": str(request.form.get("description") or "").strip(),
            "source_type": str(request.form.get("source_type") or "").strip().lower(),
            "enabled": request.form.get("enabled") == "on",
            "zabbix_url": str(request.form.get("zabbix_url") or "").strip(),
            "runner_id": str(request.form.get("runner_id") or "").strip(),
            "snmp_port": str(request.form.get("snmp_port") or "162").strip(),
            "allowed_networks": [line.strip() for line in str(request.form.get("allowed_networks") or "").splitlines() if line.strip()],
        }
    if source is None:
        return {
            "name": "", "description": "", "source_type": SOURCE_ZABBIX,
            "enabled": True, "zabbix_url": "", "runner_id": "", "snmp_port": "162", "allowed_networks": [],
        }
    return {
        "name": source.name,
        "description": source.description,
        "source_type": source.source_type,
        "enabled": source.enabled,
        "zabbix_url": source.zabbix_url,
        "runner_id": str(source.runner_id or ""),
        "snmp_port": str(source.snmp_port or 162),
        "allowed_networks": source.get_allowed_networks(),
    }


def _validate_source_form(data, source=None):
    errors = []
    if not data["name"]:
        errors.append("Source name is required.")
    duplicate = SignalSource.query.filter_by(name=data["name"])
    if source is not None:
        duplicate = duplicate.filter(SignalSource.id != source.id)
    if data["name"] and duplicate.first() is not None:
        errors.append("A Source with that name already exists.")
    if data["source_type"] not in {SOURCE_ZABBIX, SOURCE_SYSLOG, SOURCE_SNMP_TRAP}:
        errors.append("Source type must be Zabbix, Syslog, or SNMP Trap.")
    try:
        networks = validate_allowed_networks(data["allowed_networks"])
    except ReactionError as exc:
        errors.append(str(exc))
        networks = []

    runner = None
    if data["source_type"] == SOURCE_ZABBIX:
        parsed = urlsplit(data["zabbix_url"])
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            errors.append("Zabbix URL must be an https:// URL without embedded credentials.")
    elif data["source_type"] in {SOURCE_SYSLOG, SOURCE_SNMP_TRAP}:
        try:
            runner_id = int(data["runner_id"])
        except (TypeError, ValueError):
            runner_id = 0
        runner = db.session.get(Runner, runner_id) if runner_id else None
        if runner is None or runner.is_local or not runner.enabled:
            label = "Syslog" if data["source_type"] == SOURCE_SYSLOG else "SNMP Trap"
            errors.append("{} Sources require an enabled remote Runner.".format(label))

        if data["source_type"] == SOURCE_SNMP_TRAP:
            try:
                snmp_port = int(data["snmp_port"])
            except (TypeError, ValueError):
                snmp_port = 0
            if snmp_port < 1 or snmp_port > 65535:
                errors.append("SNMP listen port must be between 1 and 65535.")
            elif runner is not None and data["enabled"]:
                duplicate_port = SignalSource.query.filter_by(
                    source_type=SOURCE_SNMP_TRAP,
                    runner_id=runner.id,
                    snmp_port=snmp_port,
                    enabled=True,
                )
                if source is not None:
                    duplicate_port = duplicate_port.filter(SignalSource.id != source.id)
                if duplicate_port.first() is not None:
                    errors.append(
                        "Another enabled SNMP Trap Source already uses UDP port {} on this Runner.".format(
                            snmp_port
                        )
                    )

    return errors, networks, runner


@bp.get("/sources")
def sources():
    _admin_required()
    rows = SignalSource.query.order_by(SignalSource.name.asc()).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    rows = pagination.items
    capability_states = {}
    runners = {source.runner for source in rows if source.runner is not None}
    for runner in runners:
        capability_states[runner.id] = {
            row["key"]: row for row in runner_capability_rows(runner)
        }
    return render_template(
        "sources.html",
        sources=rows,
        capability_states=capability_states,
        pagination=pagination,
    )


@bp.route("/sources/new", methods=["GET", "POST"])
def source_new():
    _admin_required()
    data = _source_form_data()
    runners = Runner.query.filter(Runner.is_local.is_(False)).order_by(Runner.name.asc()).all()
    if request.method == "POST":
        errors, networks, runner = _validate_source_form(data)
        if not errors:
            source = SignalSource(
                name=data["name"], description=data["description"], source_type=data["source_type"],
                enabled=data["enabled"], zabbix_url=data["zabbix_url"] if data["source_type"] == SOURCE_ZABBIX else "",
                runner=runner,
                snmp_port=int(data["snmp_port"] or 162) if data["source_type"] == SOURCE_SNMP_TRAP else 162,
            )
            source.set_allowed_networks(networks)
            secret = ""
            if source.source_type == SOURCE_ZABBIX:
                secret = secrets.token_urlsafe(48)
                source.set_hmac_secret(secret)
            db.session.add(source)
            db.session.commit()
            record_audit_event("source.create", object_type="source", object_id=source.id, object_name=source.name)
            if secret:
                return render_template("source_secret.html", source=source, secret=secret, created=True)
            flash('Source "{}" created.'.format(source.name), "success")
            return redirect(url_for("main.sources"))
        for error in errors:
            flash(error, "error")
    return render_template("source_form.html", source=None, form_data=data, runners=runners)


@bp.route("/sources/<int:source_id>/edit", methods=["GET", "POST"])
def source_edit(source_id):
    _admin_required()
    source = db.get_or_404(SignalSource, source_id)
    data = _source_form_data(source)
    runners = Runner.query.filter(Runner.is_local.is_(False)).order_by(Runner.name.asc()).all()
    if request.method == "POST":
        # Source type is immutable once created: changing ingress trust semantics
        # behind an existing Source identity would be too surprising.
        data["source_type"] = source.source_type
        errors, networks, runner = _validate_source_form(data, source=source)
        if not errors:
            source.name = data["name"]
            source.description = data["description"]
            source.enabled = data["enabled"]
            source.set_allowed_networks(networks)
            if source.source_type == SOURCE_ZABBIX:
                source.zabbix_url = data["zabbix_url"]
            else:
                source.runner = runner
                if source.source_type == SOURCE_SNMP_TRAP:
                    source.snmp_port = int(data["snmp_port"] or 162)
            db.session.commit()
            record_audit_event("source.update", object_type="source", object_id=source.id, object_name=source.name)
            flash('Source "{}" updated.'.format(source.name), "success")
            return redirect(url_for("main.sources"))
        for error in errors:
            flash(error, "error")
    return render_template("source_form.html", source=source, form_data=data, runners=runners)


@bp.post("/sources/<int:source_id>/regenerate-secret")
def source_regenerate_secret(source_id):
    _admin_required()
    source = db.get_or_404(SignalSource, source_id)
    if source.source_type != SOURCE_ZABBIX:
        abort(400)
    secret = secrets.token_urlsafe(48)
    source.set_hmac_secret(secret)
    db.session.commit()
    record_audit_event("source.secret_rotate", object_type="source", object_id=source.id, object_name=source.name)
    return render_template("source_secret.html", source=source, secret=secret, created=False)


def _reactor_form_data(reactor=None):
    if request.method == "POST":
        fields = request.form.getlist("match_field")
        operators = request.form.getlist("match_operator")
        values = request.form.getlist("match_value")
        rules = []
        for field, operator, value in zip(fields, operators, values):
            field = str(field or "").strip()
            operator = str(operator or "equals").strip()
            if not field:
                continue
            rule = {"field": field, "operator": operator}
            if operator not in {"exists", "not_exists"}:
                rule["value"] = str(value or "").strip()
            rules.append(rule)
        mode_key = "any" if request.form.get("match_mode") == "any" else "all"

        recovery_rules = []
        for field, operator, value in zip(
            request.form.getlist("recovery_match_field"),
            request.form.getlist("recovery_match_operator"),
            request.form.getlist("recovery_match_value"),
        ):
            field = str(field or "").strip()
            operator = str(operator or "equals").strip()
            if not field:
                continue
            rule = {"field": field, "operator": operator}
            if operator not in {"exists", "not_exists"}:
                rule["value"] = str(value or "").strip()
            recovery_rules.append(rule)
        recovery_mode_key = "any" if request.form.get("recovery_match_mode") == "any" else "all"
        recovery_correlation_inputs = []
        for value in str(request.form.get("recovery_correlation_inputs") or "").split(","):
            value = value.strip()
            if value and value not in recovery_correlation_inputs:
                recovery_correlation_inputs.append(value)

        mappings = {}
        for variable, kind, value, pattern in zip(
            request.form.getlist("mapping_variable"),
            request.form.getlist("mapping_kind"),
            request.form.getlist("mapping_value"),
            request.form.getlist("mapping_pattern"),
        ):
            variable = str(variable or "").strip()
            kind = str(kind or "").strip()
            if not variable or kind == "none":
                continue
            if kind == "signal":
                mapping = {"kind": "signal", "path": str(value or "").strip()}
                pattern = str(pattern or "").strip()
                if pattern:
                    mapping["pattern"] = pattern
                mappings[variable] = mapping
            else:
                mappings[variable] = {"kind": "constant", "value": str(value or "")}

        return {
            "name": str(request.form.get("name") or "").strip(),
            "description": str(request.form.get("description") or "").strip(),
            "enabled": request.form.get("enabled") == "on",
            "mode": str(request.form.get("mode") or REACTOR_OBSERVE).strip(),
            "source_id": str(request.form.get("source_id") or "").strip(),
            "package_id": str(request.form.get("package_id") or "").strip(),
            "match_mode": mode_key,
            "rules": rules,
            "mappings": mappings,
            "recovery_window_seconds": str(request.form.get("recovery_window_seconds") or "0").strip(),
            "recovery_match_mode": recovery_mode_key,
            "recovery_rules": recovery_rules,
            "recovery_correlation_inputs": recovery_correlation_inputs,
            "cooldown_seconds": str(request.form.get("cooldown_seconds") or "0").strip(),
            "max_concurrency": str(request.form.get("max_concurrency") or "1").strip(),
        }
    if reactor is None:
        return {"name":"", "description":"", "enabled":True, "mode":REACTOR_OBSERVE, "source_id":"", "package_id":"", "match_mode":"all", "rules":[], "mappings":{}, "recovery_window_seconds":"0", "recovery_match_mode":"all", "recovery_rules":[], "recovery_correlation_inputs":[], "cooldown_seconds":"0", "max_concurrency":"1"}
    match = reactor.get_match()
    match_mode = "any" if "any" in match else "all"
    recovery_match = reactor.get_recovery_match()
    recovery_match_mode = "any" if "any" in recovery_match else "all"
    return {
        "name": reactor.name, "description": reactor.description, "enabled": reactor.enabled,
        "mode": reactor.mode, "source_id": str(reactor.source_id), "package_id": str(reactor.package_id),
        "match_mode": match_mode, "rules": match.get(match_mode, []), "mappings": reactor.get_mappings(),
        "recovery_window_seconds": str(reactor.recovery_window_seconds),
        "recovery_match_mode": recovery_match_mode, "recovery_rules": recovery_match.get(recovery_match_mode, []),
        "recovery_correlation_inputs": reactor.get_recovery_correlation_inputs(),
        "cooldown_seconds": str(reactor.cooldown_seconds), "max_concurrency": str(reactor.max_concurrency),
    }


def _validate_reactor_form(data, reactor=None):
    errors = []
    if not data["name"]:
        errors.append("Reactor name is required.")
    duplicate = Reactor.query.filter_by(name=data["name"])
    if reactor is not None:
        duplicate = duplicate.filter(Reactor.id != reactor.id)
    if data["name"] and duplicate.first() is not None:
        errors.append("A Reactor with that name already exists.")
    try:
        source = db.session.get(SignalSource, int(data["source_id"]))
    except (TypeError, ValueError):
        source = None
    if source is None:
        errors.append("A Source is required.")
    try:
        package = db.session.get(ProjectPackage, int(data["package_id"]))
    except (TypeError, ValueError):
        package = None
    if package is None or not package.allow_as_reaction:
        errors.append("Select a Package with Allow as Reaction enabled.")
    if data["mode"] not in {REACTOR_OBSERVE, REACTOR_AUTOMATIC}:
        errors.append("Reactor mode must be Observe or Automatic.")
    match = {data["match_mode"]: data["rules"]}
    try:
        validate_match_definition(match)
    except ReactionError as exc:
        errors.append(str(exc))
    if package is not None:
        try:
            validate_mappings(package, data["mappings"])
        except ReactionError as exc:
            errors.append(str(exc))
    try:
        recovery_window = int(data["recovery_window_seconds"])
        if recovery_window < 0 or recovery_window > 604800:
            raise ValueError
    except ValueError:
        recovery_window = 0
        errors.append("Recovery window must be between 0 and 604800 seconds.")
    data["recovery_window_seconds"] = str(recovery_window)
    if recovery_window > 0:
        recovery_match = {data["recovery_match_mode"]: data["recovery_rules"]}
        if not data["recovery_rules"]:
            errors.append("A recovery Signal match rule is required when a recovery window is enabled.")
        else:
            try:
                validate_match_definition(recovery_match)
            except ReactionError as exc:
                errors.append("Recovery match: {}".format(exc))
        if not data["recovery_correlation_inputs"]:
            errors.append("At least one recovery correlation Package input is required.")
        for name in data["recovery_correlation_inputs"]:
            mapping = data["mappings"].get(name)
            if mapping is None:
                errors.append("Recovery correlation input {} has no Reaction input mapping.".format(name))
            elif mapping.get("kind") != "signal":
                errors.append("Recovery correlation input {} must be mapped from a Signal field.".format(name))
    try:
        cooldown = max(0, int(data["cooldown_seconds"]))
    except ValueError:
        cooldown = 0
        errors.append("Cooldown must be a whole number of seconds.")
    try:
        concurrency = int(data["max_concurrency"])
        if concurrency < 1 or concurrency > 100:
            raise ValueError
    except ValueError:
        concurrency = 1
        errors.append("Maximum concurrency must be between 1 and 100.")
    return errors, source, package, match, cooldown, concurrency


def _collect_signal_field_paths(value, prefix="fields"):
    """Return Reactor-addressable paths found in a structured Signal value."""
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key = str(key or "").strip()
            if not key:
                continue
            path = "{}.{}".format(prefix, key)
            paths.add(path)
            paths.update(_collect_signal_field_paths(child, path))
    return paths


def _reactor_source_field_paths(sources):
    """Build autocomplete choices from canonical and recently observed fields."""
    common = [
        "host", "severity", "description", "signal_type",
        "signal_at", "received_at", "external_signal_id",
    ]
    known = {
        SOURCE_SNMP_TRAP: [
            "fields.snmp.agent_address",
            "fields.snmp.enterprise_oid",
            "fields.snmp.sys_uptime",
            "fields.snmp.trap_oid",
            "fields.snmp.varbinds",
        ],
        SOURCE_SYSLOG: [
            "fields.facility",
            "fields.program",
        ],
        SOURCE_ZABBIX: [],
    }
    result = {str(source.id): set(common + known.get(source.source_type, [])) for source in sources}

    # Recent Signals make the suggestions self-describing: vendor-specific
    # SNMP varbind OIDs and arbitrary source fields appear without requiring
    # Journeyman to know about them in advance.
    source_ids = [source.id for source in sources]
    if source_ids:
        recent = (
            Signal.query.filter(Signal.source_id.in_(source_ids))
            .order_by(Signal.received_at.desc())
            .limit(500)
            .all()
        )
        per_source = {}
        for signal in recent:
            count = per_source.get(signal.source_id, 0)
            if count >= 25:
                continue
            per_source[signal.source_id] = count + 1
            try:
                fields = signal.get_fields()
            except ValueError:
                continue
            result.setdefault(str(signal.source_id), set(common)).update(
                _collect_signal_field_paths(fields)
            )

    return {source_id: sorted(paths) for source_id, paths in result.items()}


def _reactor_context(data, reactor=None):
    sources = SignalSource.query.order_by(SignalSource.name.asc()).all()
    packages = ProjectPackage.query.filter(ProjectPackage.allow_as_reaction.is_(True)).order_by(ProjectPackage.name.asc()).all()
    package_inputs = {
        str(package.id): [
            {"variable_name": item.variable_name, "label": item.label, "required": item.required, "is_secret": bool(item.is_secret or item.input_type == "password")}
            for item in package.inputs
        ] for package in packages
    }
    recent_signals = []
    if reactor is not None:
        recent_signals = (
            Signal.query.filter_by(source_id=reactor.source_id)
            .order_by(Signal.received_at.desc()).limit(100).all()
        )
    return dict(
        reactor=reactor,
        form_data=data,
        sources=sources,
        packages=packages,
        package_inputs=package_inputs,
        source_field_paths=_reactor_source_field_paths(sources),
        recent_signals=recent_signals,
    )


@bp.get("/reactors")
def reactors():
    _admin_required()
    rows = Reactor.query.order_by(Reactor.name.asc()).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    return render_template("reactors.html", reactors=pagination.items, pagination=pagination)


@bp.route("/reactors/new", methods=["GET", "POST"])
def reactor_new():
    _admin_required()
    data = _reactor_form_data()
    if request.method == "POST":
        errors, source, package, match, cooldown, concurrency = _validate_reactor_form(data)
        if not errors:
            reactor = Reactor(name=data["name"], description=data["description"], enabled=data["enabled"], mode=data["mode"], source=source, package=package, recovery_window_seconds=int(data["recovery_window_seconds"]), cooldown_seconds=cooldown, max_concurrency=concurrency)
            reactor.set_match(match)
            reactor.set_mappings(data["mappings"])
            reactor.set_recovery_match({data["recovery_match_mode"]: data["recovery_rules"]})
            reactor.set_recovery_correlation_inputs(data["recovery_correlation_inputs"])
            db.session.add(reactor)
            db.session.commit()
            record_audit_event("reactor.create", object_type="reactor", object_id=reactor.id, object_name=reactor.name)
            flash('Reactor "{}" created in {} mode.'.format(reactor.name, reactor.mode.title()), "success")
            return redirect(url_for("main.reactors"))
        for error in errors:
            flash(error, "error")
    return render_template("reactor_form.html", **_reactor_context(data))


@bp.route("/reactors/<int:reactor_id>/edit", methods=["GET", "POST"])
def reactor_edit(reactor_id):
    _admin_required()
    reactor = db.get_or_404(Reactor, reactor_id)
    data = _reactor_form_data(reactor)
    if request.method == "POST":
        errors, source, package, match, cooldown, concurrency = _validate_reactor_form(data, reactor=reactor)
        if not errors:
            reactor.name=data["name"]; reactor.description=data["description"]; reactor.enabled=data["enabled"]; reactor.mode=data["mode"]
            reactor.source=source; reactor.package=package; reactor.cooldown_seconds=cooldown; reactor.max_concurrency=concurrency
            reactor.recovery_window_seconds=int(data["recovery_window_seconds"])
            reactor.set_match(match); reactor.set_mappings(data["mappings"])
            reactor.set_recovery_match({data["recovery_match_mode"]: data["recovery_rules"]})
            reactor.set_recovery_correlation_inputs(data["recovery_correlation_inputs"])
            db.session.commit()
            record_audit_event("reactor.update", object_type="reactor", object_id=reactor.id, object_name=reactor.name)
            flash('Reactor "{}" updated.'.format(reactor.name), "success")
            return redirect(url_for("main.reactors"))
        for error in errors:
            flash(error, "error")
    return render_template("reactor_form.html", **_reactor_context(data, reactor=reactor))


@bp.post("/reactors/<int:reactor_id>/delete")
def reactor_delete(reactor_id):
    _admin_required()
    reactor = db.get_or_404(Reactor, reactor_id)
    reactor_name = reactor.name
    try:
        result = delete_reactor(reactor_name)
    except ReactorConfigurationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.reactors"))

    if result.changed:
        record_audit_event(
            "reactor.delete",
            object_type="reactor",
            object_id=reactor_id,
            object_name=reactor_name,
        )
        flash('Reactor "{}" deleted.'.format(reactor_name), "success")
    else:
        flash(result.message, "info")
    return redirect(url_for("main.reactors"))


def _signals_list_fingerprint():
    """Return cheap state for immutable Signals and their Reaction counts."""
    signal_state = db.session.query(func.max(Signal.id), func.count(Signal.id)).one()
    reaction_state = db.session.query(func.max(Reaction.id), func.count(Reaction.id)).one()
    return tuple(signal_state) + tuple(reaction_state)


def _reaction_list_fingerprint():
    """Return lightweight state for recent Reactions, which change in place."""
    return tuple(
        db.session.query(
            Reaction.id,
            Reaction.status,
            Reaction.job_id,
            Reaction.message,
        )
        .order_by(Reaction.created_at.desc(), Reaction.id.desc())
        .limit(500)
        .all()
    )


def _list_update_stream(load_fingerprint, event_name):
    """Yield one SSE update when list state changes, with heartbeats."""
    initial_fingerprint = load_fingerprint()

    @stream_with_context
    def generate():
        last_fingerprint = initial_fingerprint
        heartbeat_counter = 0
        while True:
            db.session.expire_all()
            fingerprint = load_fingerprint()
            if fingerprint != last_fingerprint:
                yield "event: {}\ndata: {{}}\n\n".format(event_name)
                return
            heartbeat_counter += 1
            if heartbeat_counter >= 15:
                yield ": heartbeat\n\n"
                heartbeat_counter = 0
            time.sleep(1)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/signals")
def signals():
    _admin_required()

    page = max(request.args.get("page", 1, type=int), 1)
    query_text = str(request.args.get("q") or "").strip()
    source_id = request.args.get("source", type=int)
    severity = str(request.args.get("severity") or "").strip()

    query = Signal.query.join(SignalSource)

    if query_text:
        pattern = "%{}%".format(query_text)
        filters = [
            SignalSource.name.ilike(pattern),
            Signal.host.ilike(pattern),
            Signal.severity.ilike(pattern),
            Signal.description.ilike(pattern),
        ]
        if query_text.isdigit():
            filters.append(Signal.id == int(query_text))
        query = query.filter(or_(*filters))

    if source_id:
        query = query.filter(Signal.source_id == source_id)
    if severity:
        query = query.filter(Signal.severity == severity)

    pagination = query.order_by(
        Signal.received_at.desc(),
        Signal.id.desc(),
    ).paginate(page=page, per_page=page_size_for_user(current_username()), error_out=False)

    sources = SignalSource.query.order_by(SignalSource.name.asc()).all()
    severities = [
        row[0]
        for row in (
            db.session.query(Signal.severity)
            .filter(Signal.severity != "")
            .distinct()
            .order_by(Signal.severity.asc())
            .all()
        )
        if row[0]
    ]

    return render_template(
        "signals.html",
        signals=pagination.items,
        pagination=pagination,
        sources=sources,
        severities=severities,
        query_text=query_text,
        selected_source_id=source_id,
        selected_severity=severity,
        pagination_args={"q": query_text, "source": source_id or "", "severity": severity},
    )


@bp.get("/signals/events")
def signals_events():
    """Notify the Signals page when its rendered list data changes."""

    _admin_required()
    return _list_update_stream(_signals_list_fingerprint, "signals-update")


@bp.get("/signals/<int:signal_id>")
def signal_detail(signal_id):
    _admin_required()
    signal = db.get_or_404(Signal, signal_id)
    return render_template("signal_detail.html", signal=signal)


@bp.get("/reactions")
def reactions():
    _admin_required()
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    pagination = Reaction.query.order_by(Reaction.created_at.desc(), Reaction.id.desc()).paginate(
        page=page,
        per_page=page_size_for_user(current_username()),
        error_out=False,
    )
    return render_template("reactions.html", reactions=pagination.items, pagination=pagination)


@bp.get("/reactions/events")
def reactions_events():
    """Notify the Reactions page when its rendered list data changes."""

    _admin_required()
    return _list_update_stream(_reaction_list_fingerprint, "reactions-update")


@bp.get("/reactions/<int:reaction_id>")
def reaction_detail(reaction_id):
    """Show the complete persisted outcome of one Reactor match."""

    _admin_required()
    reaction = db.get_or_404(Reaction, reaction_id)
    return render_template("reaction_detail.html", reaction=reaction)


@bp.post("/reactors/<int:reactor_id>/test")
def reactor_test_ui(reactor_id):
    _admin_required()
    reactor = db.get_or_404(Reactor, reactor_id)
    try:
        signal_id = int(request.form.get("signal_id") or "")
    except ValueError:
        abort(400)
    signal = db.get_or_404(Signal, signal_id)
    if signal.source_id != reactor.source_id:
        abort(400)
    try:
        matched = reactor_matches(reactor, signal)
        resolved = resolve_reaction_inputs(reactor, signal) if matched else {}
        error = ""
    except ReactionError as exc:
        matched = False
        resolved = {}
        error = str(exc)
    return render_template("reactor_test.html", reactor=reactor, signal=signal, matched=matched, resolved=resolved, error=error)


@bp.post("/reactors/<int:reactor_id>/test/<int:signal_id>")
def reactor_test(reactor_id, signal_id):
    _admin_required()
    reactor = db.get_or_404(Reactor, reactor_id)
    signal = db.get_or_404(Signal, signal_id)
    if signal.source_id != reactor.source_id:
        return jsonify({"match": False, "error": "Signal belongs to a different Source."}), 400
    try:
        matched = reactor_matches(reactor, signal)
        resolved = resolve_reaction_inputs(reactor, signal) if matched else {}
        return jsonify({"match": matched, "resolved_inputs": resolved, "result": "Would invoke Reaction" if matched else "No Reaction"})
    except ReactionError as exc:
        return jsonify({"match": False, "error": str(exc)}), 400


def _record_source_rejection(source, sender_ip, reason):
    if source is not None:
        source.rejected_count = int(source.rejected_count or 0) + 1
        source.last_sender_ip = str(sender_ip or "")[:64]
        db.session.commit()
    current_app.logger.warning("Rejected Signal submission from %s: %s", sender_ip, reason)


def _persist_signal(source, payload, raw_payload, sender_ip, runner=None):
    external_id = str(payload.get("signal_id") or "").strip()
    if not external_id or len(external_id) > 255:
        raise ReactionError("signal_id is required and must be at most 255 characters.")
    existing = Signal.query.filter_by(source_id=source.id, external_signal_id=external_id).one_or_none()
    if existing is not None:
        return existing, False
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        raise ReactionError("fields must be a JSON object.")
    signal = Signal(
        source=source,
        external_signal_id=external_id,
        signal_type=str(payload.get("signal_type") or "")[:120],
        signal_at=_parse_signal_time(payload.get("timestamp")),
        host=str(payload.get("host") or "")[:255],
        severity=str(payload.get("severity") or "")[:64],
        description=str(payload.get("description") or "")[:10000],
        raw_payload=str(raw_payload or "")[:MAX_SIGNAL_BYTES],
        sender_ip=str(sender_ip or "")[:64],
        runner=runner,
    )
    signal.set_fields(fields)
    db.session.add(signal)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existing = Signal.query.filter_by(source_id=source.id, external_signal_id=external_id).one()
        return existing, False
    source.accepted_count = int(source.accepted_count or 0) + 1
    source.last_signal_at = datetime.now(timezone.utc)
    source.last_sender_ip = str(sender_ip or "")[:64]
    db.session.commit()
    process_signal(signal)
    return signal, True


@bp.post("/api/signals/zabbix")
@csrf.exempt
def zabbix_signal_api():
    raw = request.get_data(cache=True)
    if len(raw) > MAX_SIGNAL_BYTES:
        return jsonify({"error": "Signal payload is too large."}), 413
    source_uuid = str(request.headers.get("X-Journeyman-Source") or "").strip()
    source = SignalSource.query.filter_by(source_uuid=source_uuid, source_type=SOURCE_ZABBIX).one_or_none()
    sender_ip = request.remote_addr or ""
    if source is None or not source.enabled:
        _record_source_rejection(source, sender_ip, "unknown or disabled Source")
        return jsonify({"error": "Source authentication failed."}), 403
    if not request.is_secure:
        _record_source_rejection(source, sender_ip, "HTTPS required")
        return jsonify({"error": "HTTPS is required."}), 403
    if not sender_allowed(source, sender_ip):
        _record_source_rejection(source, sender_ip, "sender address not allowed")
        return jsonify({"error": "Source authentication failed."}), 403
    timestamp = str(request.headers.get("X-Journeyman-Timestamp") or "").strip()
    signature = str(request.headers.get("X-Journeyman-Signature") or "").strip().lower()
    try:
        request_epoch = int(timestamp)
    except ValueError:
        request_epoch = 0
    if not request_epoch or abs(int(time.time()) - request_epoch) > ZABBIX_TIMESTAMP_WINDOW_SECONDS:
        _record_source_rejection(source, sender_ip, "timestamp outside allowed window")
        return jsonify({"error": "Source authentication failed."}), 403
    try:
        secret = source.get_hmac_secret().encode("utf-8")
    except Exception:
        current_app.logger.exception("Unable to decrypt HMAC secret for Source %s", source.id)
        return jsonify({"error": "Source authentication unavailable."}), 503
    signed = source_uuid.encode("utf-8") + b"\n" + timestamp.encode("ascii") + b"\n" + raw
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        _record_source_rejection(source, sender_ip, "invalid HMAC")
        return jsonify({"error": "Source authentication failed."}), 403
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ReactionError("Unsupported or missing schema_version.")
        signal, created = _persist_signal(source, payload, raw.decode("utf-8"), sender_ip)
    except (UnicodeDecodeError, json.JSONDecodeError, ReactionError) as exc:
        _record_source_rejection(source, sender_ip, str(exc))
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "accepted" if created else "duplicate", "signal_id": signal.id}), 202 if created else 200


@bp.post("/api/runners/signals")
@csrf.exempt
def runner_signal_api():
    runner_uuid = str(request.headers.get("X-Journeyman-Runner-ID") or "")
    authorization = str(request.headers.get("Authorization") or "")
    secret = authorization[7:] if authorization.startswith("Bearer ") else ""
    runner = authenticate_runner(runner_uuid, secret)
    if runner is None:
        return jsonify({"error": "Runner authentication failed."}), 403
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json."}), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required."}), 400
    source_uuid = str(payload.get("source_uuid") or "").strip()
    source = SignalSource.query.filter(
        SignalSource.source_uuid == source_uuid,
        SignalSource.source_type.in_([SOURCE_SYSLOG, SOURCE_SNMP_TRAP]),
    ).one_or_none()
    if source is None or not source.enabled or source.runner_id != runner.id:
        return jsonify({"error": "Signal Source is not assigned to this Runner."}), 403
    items = payload.get("signals")
    if not isinstance(items, list) or not items or len(items) > 100:
        return jsonify({"error": "signals must contain between 1 and 100 items."}), 400
    results = []
    for item in items:
        if not isinstance(item, dict):
            return jsonify({"error": "Each Signal must be a JSON object."}), 400
        sender_ip = str(item.get("sender_ip") or "").strip()
        if not sender_allowed(source, sender_ip):
            results.append({"signal_id": str(item.get("signal_id") or ""), "status": "rejected", "error": "sender address not allowed"})
            continue
        normalized = dict(item)
        normalized.setdefault("schema_version", 1)
        normalized.setdefault("signal_type", "snmp_trap" if source.source_type == SOURCE_SNMP_TRAP else "syslog")
        raw_text = str(item.get("raw") or item.get("description") or "")
        try:
            signal, created = _persist_signal(source, normalized, raw_text, sender_ip, runner=runner)
            results.append({"signal_id": normalized.get("signal_id"), "status": "accepted" if created else "duplicate", "id": signal.id})
        except ReactionError as exc:
            results.append({"signal_id": normalized.get("signal_id"), "status": "rejected", "error": str(exc)})
    return jsonify({"results": results})
