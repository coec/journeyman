"""Declarative Reactor configuration shared by API clients."""

from dataclasses import dataclass

from app import db
from app.models import NotificationRule, ProjectPackage, Reaction, Reactor, SignalSource
from app.models.reaction import REACTION_PENDING, REACTOR_AUTOMATIC, REACTOR_OBSERVE
from app.services.reactions import ReactionError, validate_match_definition, validate_mappings


class ReactorConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ReactorConfigurationResult:
    reactor: Reactor | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def reactor_configuration_document(reactor):
    return {
        "id": reactor.id,
        "name": reactor.name,
        "description": reactor.description or "",
        "enabled": bool(reactor.enabled),
        "mode": reactor.mode,
        "source": reactor.source.name if reactor.source else "",
        "package": reactor.package.name if reactor.package else "",
        "match": reactor.get_match(),
        "mappings": reactor.get_mappings(),
        "recovery_window_seconds": int(reactor.recovery_window_seconds or 0),
        "recovery_match": reactor.get_recovery_match(),
        "recovery_correlation_inputs": reactor.get_recovery_correlation_inputs(),
        "cooldown_seconds": int(reactor.cooldown_seconds or 0),
        "max_concurrency": int(reactor.max_concurrency or 1),
    }


def _normalise(values):
    if not isinstance(values, dict):
        raise ReactorConfigurationError("Reactor configuration must be a mapping.")
    name = _clean(values.get("name"))
    if not name:
        raise ReactorConfigurationError("Reactor name is required.")

    source_name = _clean(values.get("source"))
    source = SignalSource.query.filter_by(name=source_name).first() if source_name else None
    if source is None:
        raise ReactorConfigurationError('Signal Source "{}" does not exist.'.format(source_name))

    package_name = _clean(values.get("package"))
    package = ProjectPackage.query.filter_by(name=package_name).first() if package_name else None
    if package is None or not package.allow_as_reaction:
        raise ReactorConfigurationError(
            'Package "{}" does not exist or does not have Allow as Reaction enabled.'.format(package_name)
        )

    mode = _clean(values.get("mode")) or REACTOR_OBSERVE
    if mode not in {REACTOR_OBSERVE, REACTOR_AUTOMATIC}:
        raise ReactorConfigurationError("Reactor mode must be observe or automatic.")

    match = values.get("match", {"all": []})
    mappings = values.get("mappings", {})
    recovery_match = values.get("recovery_match", {"all": []})
    recovery_inputs = values.get("recovery_correlation_inputs") or []
    if not isinstance(recovery_inputs, list):
        raise ReactorConfigurationError("recovery_correlation_inputs must be a list.")
    recovery_inputs = list(dict.fromkeys(_clean(value) for value in recovery_inputs if _clean(value)))
    try:
        validate_match_definition(match)
        validate_mappings(package, mappings)
    except ReactionError as exc:
        raise ReactorConfigurationError(str(exc)) from exc

    try:
        recovery_window = int(values.get("recovery_window_seconds", 0))
    except (TypeError, ValueError) as exc:
        raise ReactorConfigurationError("Recovery window must be a whole number of seconds.") from exc
    if recovery_window < 0 or recovery_window > 604800:
        raise ReactorConfigurationError("Recovery window must be between 0 and 604800 seconds.")
    if recovery_window:
        try:
            validate_match_definition(recovery_match)
        except ReactionError as exc:
            raise ReactorConfigurationError("Recovery match: {}".format(exc)) from exc
        rules = recovery_match.get("all", recovery_match.get("any", [])) if isinstance(recovery_match, dict) else []
        if not rules:
            raise ReactorConfigurationError("A recovery Signal match rule is required when a recovery window is enabled.")
        if not recovery_inputs:
            raise ReactorConfigurationError("At least one recovery correlation Package input is required.")
        for input_name in recovery_inputs:
            mapping = mappings.get(input_name)
            if mapping is None:
                raise ReactorConfigurationError(
                    "Recovery correlation input {} has no Reaction input mapping.".format(input_name)
                )
            if mapping.get("kind") != "signal":
                raise ReactorConfigurationError(
                    "Recovery correlation input {} must be mapped from a Signal field.".format(input_name)
                )
    else:
        recovery_match = {"all": []}
        recovery_inputs = []

    try:
        cooldown = int(values.get("cooldown_seconds", 0))
    except (TypeError, ValueError) as exc:
        raise ReactorConfigurationError("Cooldown must be a whole number of seconds.") from exc
    if cooldown < 0:
        raise ReactorConfigurationError("Cooldown must be zero or greater.")

    try:
        max_concurrency = int(values.get("max_concurrency", 1))
    except (TypeError, ValueError) as exc:
        raise ReactorConfigurationError("Maximum concurrency must be a whole number.") from exc
    if max_concurrency < 1 or max_concurrency > 100:
        raise ReactorConfigurationError("Maximum concurrency must be between 1 and 100.")

    return {
        "name": name,
        "description": _clean(values.get("description")),
        "enabled": bool(values.get("enabled", True)),
        "mode": mode,
        "source": source,
        "package": package,
        "match": match,
        "mappings": mappings,
        "recovery_window_seconds": recovery_window,
        "recovery_match": recovery_match,
        "recovery_correlation_inputs": recovery_inputs,
        "cooldown_seconds": cooldown,
        "max_concurrency": max_concurrency,
    }


def configure_reactor(values):
    desired = _normalise(values)
    reactor = Reactor.query.filter_by(name=desired["name"]).first()
    created = reactor is None
    if created:
        reactor = Reactor(name=desired["name"], source_id=desired["source"].id, package_id=desired["package"].id)
        db.session.add(reactor)
        current = None
    else:
        current = reactor_configuration_document(reactor)
        current = {key: current[key] for key in (
            "name", "description", "enabled", "mode", "source", "package", "match", "mappings",
            "recovery_window_seconds", "recovery_match", "recovery_correlation_inputs", "cooldown_seconds",
            "max_concurrency",
        )}

    comparable = {
        "name": desired["name"],
        "description": desired["description"],
        "enabled": desired["enabled"],
        "mode": desired["mode"],
        "source": desired["source"].name,
        "package": desired["package"].name,
        "match": desired["match"],
        "mappings": desired["mappings"],
        "recovery_window_seconds": desired["recovery_window_seconds"],
        "recovery_match": desired["recovery_match"],
        "recovery_correlation_inputs": desired["recovery_correlation_inputs"],
        "cooldown_seconds": desired["cooldown_seconds"],
        "max_concurrency": desired["max_concurrency"],
    }
    changed = created or current != comparable
    if not changed:
        return ReactorConfigurationResult(reactor, False, 'Reactor "{}" is already configured.'.format(reactor.name))

    reactor.name = desired["name"]
    reactor.description = desired["description"]
    reactor.enabled = desired["enabled"]
    reactor.mode = desired["mode"]
    reactor.source = desired["source"]
    reactor.package = desired["package"]
    reactor.set_match(desired["match"])
    reactor.set_mappings(desired["mappings"])
    reactor.recovery_window_seconds = desired["recovery_window_seconds"]
    reactor.set_recovery_match(desired["recovery_match"])
    reactor.set_recovery_correlation_inputs(desired["recovery_correlation_inputs"])
    reactor.cooldown_seconds = desired["cooldown_seconds"]
    reactor.max_concurrency = desired["max_concurrency"]
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ReactorConfigurationError("Unable to save Reactor configuration.") from exc
    return ReactorConfigurationResult(
        reactor, True, 'Reactor "{}" {}.'.format(reactor.name, "created" if created else "updated")
    )


def delete_reactor(name):
    name = _clean(name)
    reactor = Reactor.query.filter_by(name=name).first()
    if reactor is None:
        return ReactorConfigurationResult(None, False, 'Reactor "{}" is already absent.'.format(name))
    pending = (
        Reaction.query.filter_by(reactor_id=reactor.id, status=REACTION_PENDING)
        .order_by(Reaction.id.asc())
        .first()
    )
    if pending is not None:
        raise ReactorConfigurationError(
            'Reactor "{}" cannot be deleted while pending Reaction #{} is waiting '
            'for its recovery window to expire.'.format(name, pending.id)
        )

    # Preserve immutable identity for historical Reactions before the live
    # Reactor foreign key is cleared by ON DELETE SET NULL.  This also repairs
    # rows created before Reaction snapshot columns existed.
    for reaction in Reaction.query.filter_by(reactor_id=reactor.id).all():
        reaction.reactor_name_snapshot = reaction.reactor_name_snapshot or reactor.name
        reaction.source_name_snapshot = (
            reaction.source_name_snapshot
            or (reaction.signal.source.name if reaction.signal and reaction.signal.source else "")
            or (reactor.source.name if reactor.source else "")
        )
        reaction.package_name_snapshot = (
            reaction.package_name_snapshot
            or (reaction.package.name if reaction.package else "")
            or (reactor.package.name if reactor.package else "")
        )
        # Clear the relationship explicitly as well as relying on the database
        # SET NULL action.  This keeps the in-memory ORM state correct and also
        # makes SQLite development databases safe if foreign-key enforcement
        # has been temporarily disabled for maintenance.
        reaction.reactor = None

    # NotificationRule uses a generic scope_type/scope_id pair rather than a
    # foreign key.  Remove Reactor-scoped rules explicitly so deleting a
    # Reactor cannot leave orphaned rules that might later match a reused id.
    NotificationRule.query.filter_by(
        scope_type="reactor",
        scope_id=reactor.id,
    ).delete(synchronize_session=False)

    db.session.delete(reactor)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ReactorConfigurationError('Unable to delete Reactor "{}".'.format(name)) from exc
    return ReactorConfigurationResult(None, True, 'Reactor "{}" deleted.'.format(name))
