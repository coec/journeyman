import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone

from werkzeug.datastructures import MultiDict

from app import db
from app.models import Job, ProjectPackage, Reaction, Reactor, Signal, SignalSource
from app.models.project_package import (
    PACKAGE_INPUT_BOOLEAN,
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
    PACKAGE_INPUT_PASSWORD,
)
from app.models.reaction import (
    REACTION_FAILED,
    REACTION_OBSERVED,
    REACTION_PENDING,
    REACTION_QUEUED,
    REACTION_SUPPRESSED,
    REACTOR_AUTOMATIC,
    REACTOR_OBSERVE,
)
from app.services.audit import record_audit_event
from app.services.project_package_launch import prepare_package_launch
from app.services.project_execution import ProjectExecutionQueueError
from app.services.project_execution_preview import ProjectExecutionPreviewError


class ReactionError(ValueError):
    pass


_MISSING = object()

# Reactor regexes are administrator-defined, but they are evaluated against
# externally supplied Signal values. Bound both the expression and searched
# scalar so extraction cannot consume unbounded input.
MAX_MAPPING_PATTERN_LENGTH = 256
MAX_MAPPING_PATTERN_SOURCE_LENGTH = 4096


def utcnow():
    return datetime.now(timezone.utc)


def sender_allowed(source, sender_ip):
    """Return whether an inbound sender belongs to a Source allowlist."""
    networks = source.get_allowed_networks()
    if not networks:
        return False
    try:
        address = ipaddress.ip_address(str(sender_ip or "").strip())
    except ValueError:
        return False
    for raw_network in networks:
        try:
            network = ipaddress.ip_network(raw_network, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def validate_allowed_networks(values):
    result = []
    for value in values:
        value = str(value or "").strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ReactionError("Invalid allowed network: {}".format(value)) from exc
        canonical = str(network)
        if canonical not in result:
            result.append(canonical)
    if not result:
        raise ReactionError("At least one allowed sender IP or network is required.")
    return result


def signal_document(signal):
    return {
        "id": signal.id,
        "external_signal_id": signal.external_signal_id,
        "signal_type": signal.signal_type,
        "host": signal.host,
        "severity": signal.severity,
        "description": signal.description,
        "signal_at": signal.signal_at.isoformat() if signal.signal_at else None,
        "received_at": signal.received_at.isoformat() if signal.received_at else None,
        "fields": signal.get_fields(),
    }


def _path_value(document, path):
    path = str(path or "").strip()
    if not path:
        return _MISSING
    current = document
    components = path.split(".")
    index = 0
    while index < len(components):
        if not isinstance(current, dict):
            return _MISSING
        component = components[index]
        if component in current:
            current = current[component]
            index += 1
            continue

        # Signal Sources such as SNMP naturally have dictionary keys that are
        # dotted identifiers (numeric OIDs).  If a normal component does not
        # exist, consume the longest dotted key present at the current level.
        matched = False
        for end in range(len(components), index + 1, -1):
            candidate = ".".join(components[index:end])
            if candidate in current:
                current = current[candidate]
                index = end
                matched = True
                break
        if not matched:
            return _MISSING
    return current


def _compare(actual, operator, expected):
    if operator == "exists":
        return actual is not _MISSING
    if operator == "not_exists":
        return actual is _MISSING
    if actual is _MISSING:
        return False

    if operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        return {
            "greater_than": left > right,
            "greater_than_or_equal": left >= right,
            "less_than": left < right,
            "less_than_or_equal": left <= right,
        }[operator]

    if isinstance(actual, (list, tuple, set)):
        candidates = list(actual)
    else:
        candidates = [actual]

    expected_text = str(expected if expected is not None else "").casefold()
    candidate_text = [str(value if value is not None else "").casefold() for value in candidates]

    if operator == "equals":
        return any(value == expected_text for value in candidate_text)
    if operator == "not_equals":
        return all(value != expected_text for value in candidate_text)
    if operator == "contains":
        return any(expected_text in value for value in candidate_text)
    if operator == "starts_with":
        return any(value.startswith(expected_text) for value in candidate_text)
    if operator == "ends_with":
        return any(value.endswith(expected_text) for value in candidate_text)
    raise ReactionError("Unsupported Reactor operator: {}".format(operator))


def match_definition_matches(definition, signal):
    """Evaluate one deliberately small, declarative Signal match tree."""
    document = signal_document(signal)

    def evaluate(node):
        if not isinstance(node, dict):
            raise ReactionError("Reactor match nodes must be objects.")
        if "all" in node:
            rules = node["all"]
            if not isinstance(rules, list):
                raise ReactionError("Reactor 'all' must be a list.")
            return all(evaluate(item) for item in rules)
        if "any" in node:
            rules = node["any"]
            if not isinstance(rules, list):
                raise ReactionError("Reactor 'any' must be a list.")
            return any(evaluate(item) for item in rules)

        field = str(node.get("field") or "").strip()
        operator = str(node.get("operator") or "equals").strip()
        if not field:
            raise ReactionError("Reactor match rule field is required.")
        return _compare(_path_value(document, field), operator, node.get("value"))

    return evaluate(definition)


def reactor_matches(reactor, signal):
    return match_definition_matches(reactor.get_match(), signal)


def validate_match_definition(value):
    allowed = {
        "equals", "not_equals", "contains", "starts_with", "ends_with",
        "exists", "not_exists", "greater_than", "greater_than_or_equal",
        "less_than", "less_than_or_equal",
    }

    def walk(node):
        if not isinstance(node, dict):
            raise ReactionError("Each Reactor match node must be an object.")
        logical = [name for name in ("all", "any") if name in node]
        if logical:
            if len(logical) != 1 or len(node) != 1:
                raise ReactionError("A Reactor match group must contain only 'all' or 'any'.")
            children = node[logical[0]]
            if not isinstance(children, list):
                raise ReactionError("Reactor match groups must contain a list.")
            for child in children:
                walk(child)
            return
        field = str(node.get("field") or "").strip()
        operator = str(node.get("operator") or "equals").strip()
        if not field:
            raise ReactionError("Reactor match rule field is required.")
        if operator not in allowed:
            raise ReactionError("Unsupported Reactor operator: {}".format(operator))
        if operator not in {"exists", "not_exists"} and "value" not in node:
            raise ReactionError("Reactor match rule {} requires a value.".format(field))

    walk(value)
    return value


def _compile_mapping_pattern(pattern):
    pattern = str(pattern or "")
    if not pattern:
        return None
    if len(pattern) > MAX_MAPPING_PATTERN_LENGTH:
        raise ReactionError(
            "Reaction input extraction pattern must not exceed {} characters."
            .format(MAX_MAPPING_PATTERN_LENGTH)
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ReactionError(
            "Reaction input extraction pattern is invalid: {}".format(exc)
        ) from exc
    if compiled.groups != 1:
        raise ReactionError(
            "Reaction input extraction pattern must contain exactly one "
            "capturing group."
        )
    return compiled


def _apply_mapping_pattern(value, pattern, input_name="input"):
    compiled = _compile_mapping_pattern(pattern)
    if compiled is None:
        return value
    if isinstance(value, (dict, list, tuple, set)):
        raise ReactionError(
            "Regex extraction for Package input {} requires a scalar Signal "
            "field.".format(input_name)
        )
    text = str(value if value is not None else "")
    if len(text) > MAX_MAPPING_PATTERN_SOURCE_LENGTH:
        raise ReactionError(
            "Signal field used for regex extraction for Package input {} "
            "exceeds {} characters.".format(
                input_name, MAX_MAPPING_PATTERN_SOURCE_LENGTH
            )
        )
    match = compiled.search(text)
    if match is None:
        raise ReactionError(
            "Regex extraction for Package input {} did not match the Signal "
            "field.".format(input_name)
        )
    return match.group(1).strip()


def validate_mappings(package, mappings):
    if not isinstance(mappings, dict):
        raise ReactionError("Reaction input mappings must be an object.")
    inputs = {item.variable_name: item for item in package.inputs}
    unknown = sorted(set(mappings) - set(inputs))
    if unknown:
        raise ReactionError("Mappings reference unknown Package input(s): {}.".format(", ".join(unknown)))
    for name, mapping in mappings.items():
        if not isinstance(mapping, dict):
            raise ReactionError("Mapping for {} must be an object.".format(name))
        package_input = inputs[name]
        if package_input.is_secret or package_input.input_type == PACKAGE_INPUT_PASSWORD:
            raise ReactionError("Secret Package input {} cannot be populated by a Reactor.".format(name))
        kind = str(mapping.get("kind") or "").strip()
        if kind == "signal":
            if not str(mapping.get("path") or "").strip():
                raise ReactionError("Signal mapping for {} requires a path.".format(name))
            if mapping.get("pattern"):
                _compile_mapping_pattern(mapping.get("pattern"))
        elif kind == "constant":
            if "value" not in mapping:
                raise ReactionError("Constant mapping for {} requires a value.".format(name))
            if mapping.get("pattern"):
                raise ReactionError("Regex extraction is only valid for Signal mappings.")
        else:
            raise ReactionError("Mapping for {} must use kind 'signal' or 'constant'.".format(name))
    return mappings


def resolve_reaction_inputs(reactor, signal):
    package = reactor.package
    document = signal_document(signal)
    mappings = validate_mappings(package, reactor.get_mappings())
    resolved = {}
    for name, mapping in mappings.items():
        if mapping["kind"] == "signal":
            value = _path_value(document, mapping["path"])
            if value is _MISSING:
                raise ReactionError("Signal field {} required for Package input {} is missing.".format(mapping["path"], name))
            resolved[name] = _apply_mapping_pattern(
                value, mapping.get("pattern"), input_name=name
            )
        else:
            resolved[name] = mapping.get("value")
    return resolved


def recovery_correlation_values(reactor, signal):
    """Resolve Signal-backed Package inputs used to correlate a recovery."""
    names = reactor.get_recovery_correlation_inputs()
    if not names:
        raise ReactionError("Recovery correlation requires at least one Package input.")
    document = signal_document(signal)
    mappings = validate_mappings(reactor.package, reactor.get_mappings())
    resolved = {}
    for name in names:
        mapping = mappings.get(name)
        if mapping is None:
            raise ReactionError(
                "Recovery correlation input {} has no Reaction input mapping.".format(name)
            )
        if mapping.get("kind") != "signal":
            raise ReactionError(
                "Recovery correlation input {} must be mapped from a Signal field.".format(name)
            )
        value = _path_value(document, mapping.get("path"))
        if value is _MISSING:
            raise ReactionError(
                "Recovery Signal field {} required for correlation input {} is missing.".format(
                    mapping.get("path"), name
                )
            )
        resolved[name] = _apply_mapping_pattern(
            value, mapping.get("pattern"), input_name=name
        )
    return resolved


def _reaction_form(package, values):
    """Translate typed Reactor values into the existing Package launch contract."""
    form = MultiDict()
    for package_input in package.inputs:
        if package_input.variable_name not in values:
            continue
        value = values[package_input.variable_name]
        key = "package_value_{}".format(package_input.id)
        if package_input.input_type == PACKAGE_INPUT_BOOLEAN:
            if isinstance(value, str):
                boolean_value = value.strip().casefold() in {"1", "true", "yes", "on"}
            else:
                boolean_value = bool(value)
            form[key] = "true" if boolean_value else "false"
        elif package_input.input_type == PACKAGE_INPUT_CHOICE:
            choice_value = value
            if isinstance(value, str):
                try:
                    choice_value = json.loads(value)
                except json.JSONDecodeError:
                    choice_value = value
            form[key] = json.dumps(choice_value, ensure_ascii=False, separators=(",", ":"))
        elif package_input.input_type == PACKAGE_INPUT_EMAIL_ADDRESSES:
            form[key] = ",".join(value) if isinstance(value, list) else str(value)
        else:
            form[key] = str(value)
    return form


def prepare_reaction_package(reactor, signal):
    package = reactor.package
    if package is None or not package.enabled:
        raise ReactionError("Reaction Package is disabled or unavailable.")
    if not package.allow_as_reaction:
        raise ReactionError("Package {!r} does not allow use as a Reaction.".format(package.name))
    values = resolve_reaction_inputs(reactor, signal)
    return values, prepare_reaction_package_values(reactor, values)


def prepare_reaction_package_values(reactor, values):
    package = reactor.package
    if package is None or not package.enabled:
        raise ReactionError("Reaction Package is disabled or unavailable.")
    if not package.allow_as_reaction:
        raise ReactionError("Package {!r} does not allow use as a Reaction.".format(package.name))
    errors, _fields, prepared = prepare_package_launch(
        package=package, form=_reaction_form(package, values)
    )
    if errors:
        raise ReactionError("Reaction Package inputs are invalid: {}".format("; ".join(errors)))
    return prepared.execution_data


def _queue_reaction(reaction, execution_data):
    reactor = reaction.reactor
    signal = reaction.signal
    from app import routes as legacy_routes
    preview = legacy_routes.build_project_execution_preview(
        reactor.package.project,
        refresh_repositories=True,
        refresh_inventory_sources=True,
        step_limit_override=execution_data.step_limit or None,
        inventory_bindings=execution_data.inventory_bindings,
    )
    job = legacy_routes.queue_project_execution(
        project=reactor.package.project,
        requested_by="reactor:{}".format(reactor.name),
        message='Reaction from Reactor "{}" for Signal #{}.'.format(reactor.name, signal.id),
        resolved_inventory_data=preview.resolved_inventory_data,
        package_execution=execution_data,
    )
    reaction.job = job
    reaction.execute_after = None
    reaction.status = REACTION_QUEUED
    reaction.message = "Reaction queued as Job #{}.".format(job.id)
    db.session.flush()
    record_audit_event(
        "reactor.react",
        result="queued",
        object_type="reactor",
        object_id=reactor.id,
        object_name=reactor.name,
        actor_username="reactor:{}".format(reactor.name),
        authenticated_via="signal",
        details={
            "signal_id": signal.id, "reaction_id": reaction.id,
            "package_id": reactor.package_id, "job_id": job.id,
        },
    )
    return reaction


def _automatic_suppression(reactor, signal):
    now = utcnow()
    if reactor.cooldown_seconds > 0 and signal.host:
        threshold = now - timedelta(seconds=reactor.cooldown_seconds)
        recent = (
            Reaction.query.join(Signal, Reaction.signal_id == Signal.id)
            .filter(
                Reaction.reactor_id == reactor.id,
                Signal.host == signal.host,
                Reaction.created_at >= threshold,
                Reaction.status == REACTION_QUEUED,
            ).first()
        )
        if recent is not None:
            return "Cooldown active for host {}.".format(signal.host)

    active = (
        Reaction.query.join(Job, Reaction.job_id == Job.id)
        .filter(
            Reaction.reactor_id == reactor.id,
            Job.status.in_(("queued", "running", "waiting_oversight", "cancelling")),
        ).count()
    )
    if active >= max(1, int(reactor.max_concurrency or 1)):
        return "Maximum concurrent Reactions ({}) reached.".format(reactor.max_concurrency)
    return ""


def suppress_pending_reactions(reactor, recovery_signal):
    """Suppress pending delayed Reactions correlated with a recovery Signal."""
    if int(reactor.recovery_window_seconds or 0) <= 0:
        return []
    if not match_definition_matches(reactor.get_recovery_match(), recovery_signal):
        return []
    try:
        recovery_values = recovery_correlation_values(reactor, recovery_signal)
    except ReactionError:
        # A recovery-looking Signal without correlation fields must never cancel
        # unrelated pending work.
        return []

    received_at = recovery_signal.received_at or utcnow()
    names = reactor.get_recovery_correlation_inputs()
    pending = (
        Reaction.query
        .filter(
            Reaction.reactor_id == reactor.id,
            Reaction.status == REACTION_PENDING,
            Reaction.execute_after.is_not(None),
            Reaction.execute_after >= received_at,
            Reaction.created_at <= received_at,
        )
        .order_by(Reaction.created_at.asc(), Reaction.id.asc())
        .all()
    )
    suppressed = []
    for reaction in pending:
        trigger_values = reaction.get_resolved_inputs()
        if not all(trigger_values.get(name) == recovery_values.get(name) for name in names):
            continue
        reaction.status = REACTION_SUPPRESSED
        reaction.recovery_signal_id = recovery_signal.id
        reaction.suppressed_at = received_at
        reaction.execute_after = None
        reaction.message = (
            "Suppressed by matching recovery Signal #{} within the configured "
            "{} second recovery window."
        ).format(recovery_signal.id, reactor.recovery_window_seconds)
        suppressed.append(reaction)
        record_audit_event(
            "reactor.recovery", result="suppressed", object_type="reactor",
            object_id=reactor.id, object_name=reactor.name,
            actor_username="reactor:{}".format(reactor.name), authenticated_via="signal",
            details={
                "signal_id": reaction.signal_id,
                "recovery_signal_id": recovery_signal.id,
                "reaction_id": reaction.id,
                "correlation_inputs": names,
            },
        )
    if suppressed:
        db.session.flush()
    return suppressed


def run_reactor(reactor, signal):
    if not reactor.enabled or reactor.source_id != signal.source_id:
        return None
    if not reactor_matches(reactor, signal):
        return None

    reaction = Reaction(
        signal=signal,
        reactor=reactor,
        package=reactor.package,
        reactor_name_snapshot=reactor.name,
        source_name_snapshot=reactor.source.name if reactor.source else "",
        package_name_snapshot=reactor.package.name if reactor.package else "",
        mode=reactor.mode,
        status=REACTION_OBSERVED if reactor.mode == REACTOR_OBSERVE else REACTION_FAILED,
    )
    db.session.add(reaction)

    try:
        values, execution_data = prepare_reaction_package(reactor, signal)
        reaction.set_resolved_inputs(values)

        if reactor.mode == REACTOR_OBSERVE:
            reaction.status = REACTION_OBSERVED
            reaction.message = "Matched in Observe mode; Package was not invoked."
            db.session.flush()
            return reaction

        suppression = _automatic_suppression(reactor, signal)
        if suppression:
            reaction.status = REACTION_SUPPRESSED
            reaction.message = suppression
            db.session.flush()
            return reaction

        if int(reactor.recovery_window_seconds or 0) > 0:
            reaction.status = REACTION_PENDING
            reaction.execute_after = utcnow() + timedelta(
                seconds=int(reactor.recovery_window_seconds)
            )
            reaction.message = (
                "Recovery window active for {} seconds; the Package will run "
                "only if no correlated recovery Signal arrives."
            ).format(reactor.recovery_window_seconds)
            db.session.flush()
            record_audit_event(
                "reactor.react", result="pending", object_type="reactor",
                object_id=reactor.id, object_name=reactor.name,
                actor_username="reactor:{}".format(reactor.name), authenticated_via="signal",
                details={
                    "signal_id": signal.id, "reaction_id": reaction.id,
                    "package_id": reactor.package_id,
                    "execute_after": reaction.execute_after.isoformat(),
                },
            )
            return reaction

        return _queue_reaction(reaction, execution_data)
    except (ReactionError, ProjectExecutionPreviewError, ProjectExecutionQueueError) as exc:
        reaction.status = REACTION_FAILED
        reaction.message = str(exc)
        db.session.flush()
        return reaction


def process_signal(signal):
    reactors = (
        Reactor.query.filter_by(source_id=signal.source_id, enabled=True)
        .order_by(Reactor.name.asc()).all()
    )
    for reactor in reactors:
        try:
            suppress_pending_reactions(reactor, signal)
            run_reactor(reactor, signal)
        except Exception as exc:
            # A malformed Reactor must not prevent other Reactors from seeing
            # the same accepted Signal. Persist the failure for inspection.
            reaction = Reaction(
                signal=signal,
                reactor=reactor,
                package=reactor.package,
                reactor_name_snapshot=reactor.name,
                source_name_snapshot=reactor.source.name if reactor.source else "",
                package_name_snapshot=reactor.package.name if reactor.package else "",
                mode=reactor.mode,
                status=REACTION_FAILED,
                message="Reactor evaluation failed: {}".format(exc),
            )
            db.session.add(reaction)
    signal.processing_status = "processed"
    db.session.commit()
    return signal


def process_due_reactions(limit=50):
    """Release expired recovery windows from durable database state."""
    now = utcnow()
    reaction_ids = [
        row[0]
        for row in (
            db.session.query(Reaction.id)
            .filter(
                Reaction.status == REACTION_PENDING,
                Reaction.execute_after.is_not(None),
                Reaction.execute_after <= now,
            )
            .order_by(Reaction.execute_after.asc(), Reaction.id.asc())
            .limit(max(1, min(int(limit or 50), 500)))
            .all()
        )
    ]
    processed = 0
    for reaction_id in reaction_ids:
        reaction = db.session.get(Reaction, reaction_id)
        if reaction is None or reaction.status != REACTION_PENDING:
            continue
        reactor = reaction.reactor
        try:
            if not reactor.enabled or reactor.mode != REACTOR_AUTOMATIC:
                reaction.status = REACTION_SUPPRESSED
                reaction.execute_after = None
                reaction.suppressed_at = now
                reaction.message = (
                    "Recovery window expired, but the Reactor is disabled or "
                    "no longer Automatic; the Package was not invoked."
                )
            else:
                suppression = _automatic_suppression(reactor, reaction.signal)
                if suppression:
                    reaction.status = REACTION_SUPPRESSED
                    reaction.execute_after = None
                    reaction.suppressed_at = now
                    reaction.message = suppression
                else:
                    execution_data = prepare_reaction_package_values(
                        reactor, reaction.get_resolved_inputs()
                    )
                    _queue_reaction(reaction, execution_data)
            db.session.commit()
            processed += 1
        except (ReactionError, ProjectExecutionPreviewError, ProjectExecutionQueueError) as exc:
            db.session.rollback()
            reaction = db.session.get(Reaction, reaction_id)
            if reaction is not None and reaction.status == REACTION_PENDING:
                reaction.status = REACTION_FAILED
                reaction.execute_after = None
                reaction.message = "Delayed Reaction failed: {}".format(exc)
                db.session.commit()
                processed += 1
    return processed
