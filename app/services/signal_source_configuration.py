"""Declarative Signal Source configuration shared by API clients."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from app import db
from app.models import Reactor, Runner, Signal, SignalSource
from app.models.reaction import SOURCE_SNMP_TRAP, SOURCE_SYSLOG, SOURCE_ZABBIX
from app.services.reactions import ReactionError, validate_allowed_networks


class SignalSourceConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class SignalSourceConfigurationResult:
    source: SignalSource | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def signal_source_configuration_document(source):
    return {
        "id": source.id,
        "name": source.name,
        "description": source.description or "",
        "source_type": source.source_type,
        "enabled": bool(source.enabled),
        "allowed_networks": source.get_allowed_networks(),
        "zabbix_url": source.zabbix_url or "",
        "runner": source.runner.name if source.runner else "",
        "snmp_port": int(source.snmp_port or 162),
        "hmac_secret_configured": bool(source.encrypted_hmac_secret),
    }


def _normalise(values, source=None):
    if not isinstance(values, dict):
        raise SignalSourceConfigurationError("Signal Source configuration must be a mapping.")

    name = _clean(values.get("name"))
    if not name:
        raise SignalSourceConfigurationError("Signal Source name is required.")

    source_type = _clean(values.get("source_type")) or SOURCE_ZABBIX
    if source_type not in {SOURCE_ZABBIX, SOURCE_SYSLOG, SOURCE_SNMP_TRAP}:
        raise SignalSourceConfigurationError("Source type must be zabbix, syslog, or snmp_trap.")
    if source is not None and source.source_type != source_type:
        raise SignalSourceConfigurationError("Signal Source type cannot be changed after creation.")

    try:
        networks = validate_allowed_networks(values.get("allowed_networks") or [])
    except ReactionError as exc:
        raise SignalSourceConfigurationError(str(exc)) from exc

    runner = None
    zabbix_url = ""
    snmp_port = 162
    if source_type == SOURCE_ZABBIX:
        zabbix_url = _clean(values.get("zabbix_url"))
        parsed = urlsplit(zabbix_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SignalSourceConfigurationError(
                "Zabbix URL must be an https:// URL without embedded credentials."
            )
    else:
        runner_name = _clean(values.get("runner"))
        runner = Runner.query.filter_by(name=runner_name).first() if runner_name else None
        if runner is None or runner.is_local or not runner.enabled:
            label = "Syslog" if source_type == SOURCE_SYSLOG else "SNMP Trap"
            raise SignalSourceConfigurationError(
                '{} Sources require an enabled remote Runner named "{}".'.format(label, runner_name)
            )
        if source_type == SOURCE_SNMP_TRAP:
            try:
                snmp_port = int(values.get("snmp_port", 162))
            except (TypeError, ValueError) as exc:
                raise SignalSourceConfigurationError("SNMP listen port must be an integer.") from exc
            if snmp_port < 1 or snmp_port > 65535:
                raise SignalSourceConfigurationError("SNMP listen port must be between 1 and 65535.")
            if bool(values.get("enabled", True)):
                duplicate = SignalSource.query.filter_by(
                    source_type=SOURCE_SNMP_TRAP,
                    runner_id=runner.id,
                    snmp_port=snmp_port,
                    enabled=True,
                )
                if source is not None:
                    duplicate = duplicate.filter(SignalSource.id != source.id)
                if duplicate.first() is not None:
                    raise SignalSourceConfigurationError(
                        "Another enabled SNMP Trap Source already uses UDP port {} on this Runner.".format(snmp_port)
                    )

    return {
        "name": name,
        "description": _clean(values.get("description")),
        "source_type": source_type,
        "enabled": bool(values.get("enabled", True)),
        "allowed_networks": networks,
        "zabbix_url": zabbix_url,
        "runner": runner,
        "snmp_port": snmp_port,
        "hmac_secret": _clean(values.get("hmac_secret")),
    }


def configure_signal_source(values):
    name = _clean(values.get("name")) if isinstance(values, dict) else ""
    source = SignalSource.query.filter_by(name=name).first() if name else None
    desired = _normalise(values, source=source)
    created = source is None
    if created and desired["source_type"] == SOURCE_ZABBIX and not desired["hmac_secret"]:
        raise SignalSourceConfigurationError("hmac_secret is required when creating a Zabbix Signal Source.")
    if created:
        source = SignalSource(name=desired["name"], source_type=desired["source_type"])
        db.session.add(source)

    if desired["source_type"] == SOURCE_ZABBIX:
        existing_secret = source.get_hmac_secret() if source.encrypted_hmac_secret else ""
        secret_changed = bool(desired["hmac_secret"]) and desired["hmac_secret"] != existing_secret
    else:
        secret_changed = False

    current = None if created else {
        "name": source.name,
        "description": source.description or "",
        "source_type": source.source_type,
        "enabled": bool(source.enabled),
        "allowed_networks": source.get_allowed_networks(),
        "zabbix_url": source.zabbix_url or "",
        "runner_id": source.runner_id,
        "snmp_port": int(source.snmp_port or 162),
    }
    comparable = {
        "name": desired["name"],
        "description": desired["description"],
        "source_type": desired["source_type"],
        "enabled": desired["enabled"],
        "allowed_networks": desired["allowed_networks"],
        "zabbix_url": desired["zabbix_url"],
        "runner_id": desired["runner"].id if desired["runner"] else None,
        "snmp_port": desired["snmp_port"],
    }
    changed = created or current != comparable or secret_changed
    if not changed:
        return SignalSourceConfigurationResult(source, False, 'Signal Source "{}" is already configured.'.format(name))

    source.name = desired["name"]
    source.description = desired["description"]
    source.enabled = desired["enabled"]
    source.set_allowed_networks(desired["allowed_networks"])
    source.zabbix_url = desired["zabbix_url"]
    source.runner = desired["runner"]
    source.snmp_port = desired["snmp_port"]
    if secret_changed or (created and desired["source_type"] == SOURCE_ZABBIX):
        source.set_hmac_secret(desired["hmac_secret"])

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise SignalSourceConfigurationError("Unable to save Signal Source configuration.") from exc

    return SignalSourceConfigurationResult(
        source, True, 'Signal Source "{}" {}.'.format(name, "created" if created else "updated")
    )


def delete_signal_source(name):
    name = _clean(name)
    source = SignalSource.query.filter_by(name=name).first()
    if source is None:
        return SignalSourceConfigurationResult(None, False, 'Signal Source "{}" is already absent.'.format(name))
    if Reactor.query.filter_by(source_id=source.id).first() is not None:
        raise SignalSourceConfigurationError(
            'Signal Source "{}" cannot be deleted because it is used by one or more Reactors.'.format(name)
        )
    if Signal.query.filter_by(source_id=source.id).first() is not None:
        raise SignalSourceConfigurationError(
            'Signal Source "{}" cannot be deleted because Signals have been received from it.'.format(name)
        )
    db.session.delete(source)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise SignalSourceConfigurationError('Unable to delete Signal Source "{}".'.format(name)) from exc
    return SignalSourceConfigurationResult(None, True, 'Signal Source "{}" deleted.'.format(name))
