"""Managed runner capabilities required by configured Journeyman features.

Execution capabilities (``ansible``, ``shell``) remain in Runner.capabilities_json
and are used by dispatch.  This module deliberately keeps feature/service
capabilities separate so adding a Signal receiver cannot change job routing.
"""

import hashlib
import json

from app.models import Runner, SignalSource

CAPABILITY_SYSLOG_SIGNAL_RECEIVER = "syslog_signal_receiver"
CAPABILITY_SNMP_TRAP_RECEIVER = "snmp_trap_receiver"

CAPABILITY_DEFINITIONS = {
    CAPABILITY_SYSLOG_SIGNAL_RECEIVER: {
        "label": "Syslog Signal Receiver",
        "source_types": {"syslog"},
        "packages": ["rsyslog"],
    },
    CAPABILITY_SNMP_TRAP_RECEIVER: {
        "label": "SNMP Trap Receiver",
        "source_types": {"snmp_trap"},
        "packages": ["net-snmp"],
    },
}


def required_runner_capabilities(runner):
    """Return managed capabilities required by enabled Sources on ``runner``."""

    source_types = {
        str(source_type or "").strip().lower()
        for (source_type,) in (
            SignalSource.query
            .with_entities(SignalSource.source_type)
            .filter_by(runner_id=runner.id, enabled=True)
            .all()
        )
    }
    required = set()
    for key, definition in CAPABILITY_DEFINITIONS.items():
        if source_types.intersection(definition["source_types"]):
            required.add(key)

    # Keep the SNMP capability visible long enough to reconcile stale local
    # receiver configuration after the last Source is disabled or reassigned.
    # Once the Runner reports the empty configuration fingerprint, the cleanup
    # requirement disappears on the following heartbeat.
    if CAPABILITY_SNMP_TRAP_RECEIVER not in required:
        try:
            reported = json.loads(runner.managed_capabilities_json or "{}")
        except (TypeError, ValueError):
            reported = {}
        snmp_status = reported.get(CAPABILITY_SNMP_TRAP_RECEIVER, {}) if isinstance(reported, dict) else {}
        reported_fingerprint = str(snmp_status.get("configuration_fingerprint") or "") if isinstance(snmp_status, dict) else ""
        empty_fingerprint = configuration_fingerprint([])
        if reported_fingerprint and reported_fingerprint != empty_fingerprint:
            required.add(CAPABILITY_SNMP_TRAP_RECEIVER)
    return required


def required_runner_packages(runner):
    packages = set()
    for capability in required_runner_capabilities(runner):
        packages.update(CAPABILITY_DEFINITIONS[capability].get("packages") or [])
    return sorted(packages)


def snmp_source_configuration(runner):
    """Return stable receiver configuration for enabled SNMP Sources."""

    sources = (
        SignalSource.query
        .filter_by(runner_id=runner.id, enabled=True, source_type="snmp_trap")
        .order_by(SignalSource.source_uuid.asc())
        .all()
    )
    return [
        {
            "source_uuid": source.source_uuid,
            "port": int(source.snmp_port or 162),
        }
        for source in sources
    ]



def runner_signal_spool_root(runner):
    """Return the Signal spool path used by a runner on its physical host."""

    name = str(runner.name or "").strip()
    hostname = str(runner.hostname or "").strip()
    if name and hostname and name != hostname:
        return "/var/spool/journeyman/signals-{}".format(name)
    return "/var/spool/journeyman/signals"


def snmp_host_configuration(runner):
    """Return all enabled SNMP listeners required on ``runner``'s host.

    SNMP listeners are physical-host resources.  Multiple logical development
    runners may share one host, so reconciliation must include Sources assigned
    to every Runner reporting that hostname while preserving each Source's
    per-runner spool destination.
    """

    hostname = str(runner.hostname or "").strip()
    if not hostname:
        peer_runners = [runner]
    else:
        peer_runners = (
            Runner.query
            .filter(Runner.hostname == hostname)
            .order_by(Runner.id.asc())
            .all()
        )
    rows = []
    for peer in peer_runners:
        for source in (
            SignalSource.query
            .filter_by(runner_id=peer.id, enabled=True, source_type="snmp_trap")
            .order_by(SignalSource.source_uuid.asc())
            .all()
        ):
            rows.append({
                "source_uuid": source.source_uuid,
                "port": int(source.snmp_port or 162),
                "runner_name": peer.name,
                "signal_spool_root": runner_signal_spool_root(peer),
            })
    return sorted(rows, key=lambda item: item["source_uuid"])

def configuration_fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expected_capability_fingerprint(runner, capability):
    if capability == CAPABILITY_SNMP_TRAP_RECEIVER:
        return configuration_fingerprint(snmp_source_configuration(runner))
    return ""


def parse_reported_capabilities(raw):
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def set_reported_capabilities(runner, payload):
    if not isinstance(payload, dict):
        raise ValueError("managed_capabilities must be an object.")
    cleaned = {}
    for key, value in payload.items():
        key = str(key or "").strip().lower()
        if key not in CAPABILITY_DEFINITIONS or not isinstance(value, dict):
            continue
        cleaned[key] = {
            "installed": bool(value.get("installed")),
            "healthy": bool(value.get("healthy")),
            "message": str(value.get("message") or "")[:500],
            "configuration_fingerprint": str(value.get("configuration_fingerprint") or "")[:128],
        }
    runner.managed_capabilities_json = json.dumps(cleaned, sort_keys=True)


def runner_capability_rows(runner):
    required = required_runner_capabilities(runner)
    reported = parse_reported_capabilities(runner.managed_capabilities_json)
    rows = []
    for key in sorted(required):
        definition = CAPABILITY_DEFINITIONS[key]
        status = reported.get(key, {})
        installed = bool(status.get("installed"))
        healthy = bool(status.get("healthy"))
        expected_fingerprint = expected_capability_fingerprint(runner, key)
        reported_fingerprint = str(status.get("configuration_fingerprint") or "")
        configuration_current = not expected_fingerprint or reported_fingerprint == expected_fingerprint
        if not installed or not configuration_current:
            state = "update_required"
        elif not healthy:
            state = "warning"
        else:
            state = "healthy"
        message = str(status.get("message") or "")
        if installed and not configuration_current:
            message = "Runner configuration is out of date"
        rows.append({
            "key": key,
            "label": definition["label"],
            "packages": list(definition.get("packages") or []),
            "installed": installed,
            "healthy": healthy,
            "message": message,
            "state": state,
            "configuration_current": configuration_current,
        })
    return rows


def runner_capability_update_required(runner):
    return any(row["state"] == "update_required" for row in runner_capability_rows(runner))
