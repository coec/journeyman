import hashlib
import html
import hmac
import json
import time

import pytest
from types import SimpleNamespace

from app import db
from app.models import AuditLog, Job, NotificationRule, NotificationTarget, Project, ProjectPackage, ProjectPackageInput, Reaction, Reactor, Signal, SignalSource
from app.models.project_package import PACKAGE_BINDING_EXTRA_VAR, PACKAGE_DISPLAY_OPERATIONAL_TARGET, PACKAGE_INPUT_TEXT
from app.services.reactions import (
    ReactionError,
    _apply_mapping_pattern,
    process_signal,
    reactor_matches,
    resolve_reaction_inputs,
)
from tests.checks import assert_output_contains, assert_output_equal


def test_reaction_input_regex_extracts_one_capture_group():
    description = (
        "Cisco IOS: Interface Tu25111(GRE Tunnel from LABRTR01 to "
        "KARRTR03 over Gi0/0/0): Link down"
    )
    assert _apply_mapping_pattern(
        description,
        r"Interface\s+([^(]+)",
        input_name="interface",
    ) == "Tu25111"


def test_reaction_input_regex_requires_exactly_one_capture_group():
    with pytest.raises(ReactionError, match="exactly one capturing group"):
        _apply_mapping_pattern("Interface Tu25111", r"Interface\s+\S+")


def test_reaction_input_regex_fails_when_signal_does_not_match():
    with pytest.raises(ReactionError, match="did not match"):
        _apply_mapping_pattern(
            "Cisco IOS: Link down",
            r"Interface\s+([^(]+)",
            input_name="interface",
        )


def _identity_headers(username="admin"):
    return {"X-Test-Username": username}


def _reaction_package():
    project = Project(name="Reaction Test Project", description="", enabled=True, owner="admin", security_scope="private")
    package = ProjectPackage(
        name="Extend Tablespace",
        description="",
        project=project,
        enabled=True,
        allow_as_reaction=True,
        owner="admin",
        access_mode="restricted",
        confirmation_required=True,
    )
    package.set_fixed_vars({})
    for position, variable, label in (
        (1, "hostname", "Hostname"),
        (2, "tablespace", "Tablespace"),
        (3, "growth_gb", "Growth GB"),
    ):
        item = ProjectPackageInput(
            position=position,
            variable_name=variable,
            label=label,
            help_text="",
            input_type=PACKAGE_INPUT_TEXT,
            required=True,
            is_secret=False,
            display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET if variable == "hostname" else "normal",
            binding_type=PACKAGE_BINDING_EXTRA_VAR,
        )
        item.set_choices([])
        item.set_validation({"minimum_length": 1, "maximum_length": 120})
        item.set_conditions({})
        item.set_default_value(None)
        package.inputs.append(item)
    db.session.add_all([project, package])
    db.session.commit()
    return package


def _source(name="Zabbix Test"):
    source = SignalSource(name=name, source_type="zabbix", enabled=True, zabbix_url="https://zabbix.example/")
    source.set_allowed_networks(["127.0.0.1/32"])
    source.set_hmac_secret("reaction-test-secret")
    db.session.add(source)
    db.session.commit()
    return source


def _signal(source):
    signal = Signal(
        source=source,
        external_signal_id="90001",
        signal_type="problem",
        host="dbprod04.example",
        severity="warning",
        description="Tablespace usage above threshold",
        sender_ip="127.0.0.1",
    )
    signal.set_fields({"tags": {"tablespace": "USERS"}, "values": {"usage_percent": 91.7}})
    db.session.add(signal)
    db.session.commit()
    return signal


def test_package_form_documents_allow_as_reaction(client, seeded_packages):
    response = client.get(
        "/packages/{}/edit".format(seeded_packages["user_package"]),
        headers=_identity_headers(),
    )
    body = response.get_data(as_text=True)
    assert_output_contains(
        body,
        "Allow as Reaction",
        purpose="Package administration must explicitly document the opt-in required before a Package can be invoked by a Reactor.",
    )
    assert_output_contains(
        body,
        "Allows this Package to be invoked automatically by a configured Reactor.",
        purpose="The Package checkbox must explain exactly what enabling Reaction use permits.",
    )


def test_reactor_matches_and_resolves_tablespace_parameters(app):
    with app.app_context():
        package = _reaction_package()
        source = _source()
        signal = _signal(source)
        reactor = Reactor(
            name="Tablespace Reactor",
            source=source,
            package=package,
            mode="observe",
            enabled=True,
            cooldown_seconds=1800,
            max_concurrency=1,
        )
        reactor.set_match({"all": [
            {"field": "signal_type", "operator": "equals", "value": "problem"},
            {"field": "fields.values.usage_percent", "operator": "greater_than_or_equal", "value": "90"},
        ]})
        reactor.set_mappings({
            "hostname": {"kind": "signal", "path": "host"},
            "tablespace": {"kind": "signal", "path": "fields.tags.tablespace"},
            "growth_gb": {"kind": "constant", "value": "10"},
        })
        db.session.add(reactor)
        db.session.commit()

        matched = reactor_matches(reactor, signal)
        resolved = resolve_reaction_inputs(reactor, signal)

        assert_output_equal(
            matched,
            True,
            purpose="A 91.7% tablespace Signal must satisfy a Reactor configured for usage >= 90%.",
        )
        assert_output_equal(
            resolved,
            {"hostname": "dbprod04.example", "tablespace": "USERS", "growth_gb": "10"},
            purpose="The Reactor must pass the affected host and tablespace from the Signal while supplying the configured fixed growth amount.",
        )


def test_observe_mode_records_reaction_without_job(app):
    with app.app_context():
        package = _reaction_package()
        source = _source()
        signal = _signal(source)
        reactor = Reactor(name="Observe Tablespace", source=source, package=package, mode="observe", enabled=True)
        reactor.set_match({"all": [{"field": "host", "operator": "equals", "value": "dbprod04.example"}]})
        reactor.set_mappings({
            "hostname": {"kind": "signal", "path": "host"},
            "tablespace": {"kind": "signal", "path": "fields.tags.tablespace"},
            "growth_gb": {"kind": "constant", "value": "10"},
        })
        db.session.add(reactor)
        db.session.commit()

        process_signal(signal)
        reaction = signal.reactions[0]

        assert_output_equal(
            {"status": reaction.status, "job_id": reaction.job_id, "inputs": reaction.get_resolved_inputs()},
            {"status": "observed", "job_id": None, "inputs": {"hostname": "dbprod04.example", "tablespace": "USERS", "growth_gb": "10"}},
            purpose="Observe mode must prove that matching and input resolution work while guaranteeing that no Job is queued.",
        )


def test_zabbix_hmac_ingress_accepts_and_deduplicates(app, client):
    with app.app_context():
        source = _source()
        source_uuid = source.source_uuid

    payload = {
        "schema_version": 1,
        "signal_id": "zbx-123",
        "signal_type": "problem",
        "timestamp": "2026-08-12T14:20:00+08:00",
        "host": "dbprod04.example",
        "severity": "warning",
        "description": "Tablespace usage above threshold",
        "fields": {"tags": {"tablespace": "USERS"}, "values": {"usage_percent": 91.7}},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"reaction-test-secret",
        source_uuid.encode() + b"\n" + timestamp.encode() + b"\n" + raw,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Journeyman-Source": source_uuid,
        "X-Journeyman-Timestamp": timestamp,
        "X-Journeyman-Signature": signature,
        "X-Forwarded-Proto": "https",
    }

    first = client.post("/api/signals/zabbix", data=raw, headers=headers, base_url="https://localhost")
    second = client.post("/api/signals/zabbix", data=raw, headers=headers, base_url="https://localhost")

    assert_output_equal(
        {"first_status": first.status_code, "first_body": first.get_json(), "second_status": second.status_code, "second_body": second.get_json()},
        {
            "first_status": 202,
            "first_body": {"status": "accepted", "signal_id": first.get_json()["signal_id"]},
            "second_status": 200,
            "second_body": {"status": "duplicate", "signal_id": first.get_json()["signal_id"]},
        },
        purpose="A correctly signed Zabbix Signal must be accepted once and the same Source/signal_id replay must be acknowledged as a duplicate without creating another Signal.",
    )



def test_source_edit_keeps_public_source_uuid_visible(app, client):
    with app.app_context():
        source = _source("Visible UUID Source")
        source_id = source.id
        source_uuid = source.source_uuid

    response = client.get(
        "/sources/{}/edit".format(source_id),
        headers=_identity_headers(),
    )
    body = response.get_data(as_text=True)

    assert_output_contains(
        body,
        "Source UUID",
        purpose="A Source UUID is a public integration identifier and must remain visible on the Source edit page after creation.",
    )
    assert_output_contains(
        body,
        source_uuid,
        purpose="The edit page must show the actual persisted Source UUID so an administrator can configure or repair an external sender without querying the database.",
    )


def test_signals_list_has_explicit_payload_inspection_action(app, client):
    with app.app_context():
        source = _source("Inspectable Source")
        signal = _signal(source)
        signal_id = signal.id

    response = client.get("/signals", headers=_identity_headers())
    body = response.get_data(as_text=True)

    assert_output_contains(
        body,
        "Inspect",
        purpose="The Signals list must provide an obvious action for opening the complete stored Signal rather than requiring users to infer that the Signal number is clickable.",
    )
    assert_output_contains(
        body,
        "/signals/{}".format(signal_id),
        purpose="The Inspect action must link to the Signal detail page, which displays both structured fields and the exact raw payload received from the Source.",
    )



def test_signals_list_supports_search_filters_and_pagination(app, client):
    with app.app_context():
        source_a = _source("Zabbix Routers")
        source_b = _source("Zabbix Servers")
        source_a_id = source_a.id

        for index in range(55):
            signal = Signal(
                source=source_a if index < 54 else source_b,
                external_signal_id="search-{}".format(index),
                signal_type="problem",
                host="rtr{:02d}.example".format(index) if index < 54 else "db01.example",
                severity="Average" if index < 54 else "High",
                description="GRE link down {}".format(index) if index < 54 else "Database alert",
                sender_ip="127.0.0.1",
            )
            signal.set_fields({})
            db.session.add(signal)
        db.session.commit()

    first_page = client.get("/signals", headers=_identity_headers())
    first_body = first_page.get_data(as_text=True)
    assert first_page.status_code == 200
    assert "Page 1 of" in first_body
    assert "Next" in first_body
    assert 'name="q"' in first_body
    assert 'name="source"' in first_body
    assert 'name="severity"' in first_body

    filtered = client.get(
        "/signals?q=GRE&source={}&severity=Average".format(source_a_id),
        headers=_identity_headers(),
    )
    filtered_body = filtered.get_data(as_text=True)
    assert filtered.status_code == 200
    assert "GRE link down" in filtered_body
    assert "Database alert" not in filtered_body


def test_audit_pagination_uses_visible_previous_next_controls(app, client):
    from app.models import AuditLog

    with app.app_context():
        for index in range(55):
            db.session.add(
                AuditLog(
                    actor_username="admin",
                    action="pagination.test",
                    result="success",
                    details_json="{}",
                )
            )
        db.session.commit()

    response = client.get("/audit-log", headers=_identity_headers())
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Page 1 of" in body
    assert 'class="button secondary"' in body
    assert "Next" in body

def test_signal_and_reaction_lists_use_sse_live_refresh(client):
    signals_response = client.get("/signals", headers=_identity_headers())
    reactions_response = client.get("/reactions", headers=_identity_headers())
    signals_body = signals_response.get_data(as_text=True)
    reactions_body = reactions_response.get_data(as_text=True)

    assert_output_contains(
        signals_body,
        "/signals/events",
        purpose="The Signals page must subscribe to its SSE endpoint so newly accepted Signals appear without polling or manual refresh.",
    )
    assert_output_contains(
        signals_body,
        "signals-update",
        purpose="The Signals page must reload only when the server reports that the rendered Signal list has changed.",
    )
    assert_output_contains(
        reactions_body,
        "/reactions/events",
        purpose="The Reactions page must subscribe to its SSE endpoint so new or changed Reactor outcomes appear without polling or manual refresh.",
    )
    assert_output_contains(
        reactions_body,
        "reactions-update",
        purpose="The Reactions page must reload only when the server reports that the rendered Reaction list has changed.",
    )


def test_signal_and_reaction_sse_endpoints_are_admin_only(client):
    for path in ("/signals/events", "/reactions/events"):
        response = client.get(path, headers=_identity_headers("ordinary-user"))
        assert_output_equal(
            response.status_code,
            403,
            purpose="Live Source/Reaction operational data must remain admin-only even when delivered through an SSE endpoint: {}.".format(path),
        )



def test_reactions_list_has_explicit_inspection_action(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Reaction Inspect Source")
        signal = _signal(source)
        reactor = Reactor(
            name="Inspectable Reactor",
            source=source,
            package=package,
            mode="observe",
            enabled=True,
            cooldown_seconds=0,
            max_concurrency=1,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({"hostname": {"kind": "signal", "path": "host"}})
        db.session.add(reactor)
        db.session.flush()
        reaction = Reaction(
            signal=signal,
            reactor=reactor,
            package=package,
            mode="observe",
            status="observed",
            message="Matched in Observe mode; Package was not invoked.",
        )
        db.session.add(reaction)
        reaction.set_resolved_inputs({"hostname": signal.host})
        db.session.commit()
        reaction_id = reaction.id
        signal_host = signal.host

    response = client.get("/reactions", headers=_identity_headers())
    body = response.get_data(as_text=True)

    assert_output_contains(
        body,
        "Inspect",
        purpose="The Reactions list must provide an explicit way to inspect the complete persisted Reactor outcome.",
    )
    assert_output_contains(
        body,
        "/reactions/{}".format(reaction_id),
        purpose="The Inspect action must link to the Reaction detail page for the selected Reaction.",
    )

    detail = client.get("/reactions/{}".format(reaction_id), headers=_identity_headers())
    detail_body = detail.get_data(as_text=True)
    assert_output_contains(
        detail_body,
        "Resolved Reaction inputs",
        purpose="Reaction inspection must show the exact Package inputs resolved from the matching Signal and Reactor mappings.",
    )
    assert_output_contains(
        detail_body,
        signal_host,
        purpose="Reaction inspection must expose the concrete resolved input value so administrators can understand what the Reaction would or did pass to the Package.",
    )


def test_signal_and_reaction_timestamps_are_rendered_in_browser_local_time(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Local Time Source")
        signal = _signal(source)
        reactor = Reactor(
            name="Local Time Reactor",
            source=source,
            package=package,
            mode="observe",
            enabled=True,
            cooldown_seconds=0,
            max_concurrency=1,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.flush()
        reaction = Reaction(
            signal=signal,
            reactor=reactor,
            package=package,
            mode="observe",
            status="observed",
            message="Observed.",
        )
        reaction.set_resolved_inputs({})
        db.session.add(reaction)
        db.session.commit()
        signal_id = signal.id
        reaction_id = reaction.id

    for path in (
        "/signals",
        "/signals/{}".format(signal_id),
        "/reactions",
        "/reactions/{}".format(reaction_id),
    ):
        response = client.get(path, headers=_identity_headers())
        body = response.get_data(as_text=True)
        assert_output_contains(
            body,
            'class="utc-datetime"',
            purpose="Signal and Reaction timestamps must use Journeyman's existing browser-local datetime renderer instead of displaying raw UTC database timestamps: {}.".format(path),
        )
        assert_output_contains(
            body,
            'data-utc=',
            purpose="The local-time renderer must retain the original UTC timestamp as machine-readable input so the browser can correctly apply the operator's local timezone: {}.".format(path),
        )



def test_linked_reaction_tracks_job_lifecycle(app):
    with app.app_context():
        package = _reaction_package()
        source = _source("Lifecycle Source")
        signal = _signal(source)
        reactor = Reactor(
            name="Lifecycle Reactor",
            source=source,
            package=package,
            mode="automatic",
            enabled=True,
            cooldown_seconds=0,
            max_concurrency=1,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.flush()
        job = Job(
            project=package.project,
            project_name=package.project.name,
            status="queued",
            requested_by="reactor:Lifecycle Reactor",
        )
        db.session.add(job)
        db.session.flush()
        reaction = Reaction(
            signal=signal,
            reactor=reactor,
            package=package,
            job=job,
            mode="automatic",
            status="queued",
            message="Reaction queued as Job #{}.".format(job.id),
        )
        db.session.add(reaction)
        db.session.commit()

        observed = []
        for job_status in ("running", "cancelling", "cancelled"):
            job.status = job_status
            db.session.flush()
            observed.append((job_status, reaction.status, reaction.message))

        assert_output_equal(
            observed,
            [
                ("running", "running", "Reaction running as Job #{}.".format(job.id)),
                ("cancelling", "cancelling", "Reaction Job #{} is being cancelled.".format(job.id)),
                ("cancelled", "cancelled", "Reaction cancelled as Job #{}.".format(job.id)),
            ],
            purpose="A persisted Automatic Reaction must follow its linked Job lifecycle instead of remaining permanently Queued after execution starts or finishes.",
        )

        job.status = "successful"
        db.session.flush()
        assert_output_equal(
            {"reaction_status": reaction.status, "reaction_message": reaction.message},
            {
                "reaction_status": "successful",
                "reaction_message": "Reaction completed successfully as Job #{}.".format(job.id),
            },
            purpose="A successfully completed Job must leave an independently auditable Successful Reaction record.",
        )


def test_reaction_inspect_documents_linked_job_execution_output():
    template = open("app/templates/reaction_detail.html", encoding="utf-8").read()

    assert_output_contains(
        template,
        "Reaction execution",
        purpose="Automatic Reaction inspection must expose the normal Journeyman Job created by the Reactor rather than stopping at a Job-number link.",
    )
    assert_output_contains(
        template,
        "Playbook output",
        purpose="Reaction inspection must make the Ansible playbook stdout/stderr directly visible for operational diagnosis.",
    )
    assert_output_contains(
        template,
        "step.stdout",
        purpose="Local-runner Reaction inspection must render the stdout persisted on the linked Job step.",
    )
    assert_output_contains(
        template,
        "execution_slice.stdout",
        purpose="Sliced or remote Reaction inspection must render output from the actual execution slice/runner as well as local Job output.",
    )


def test_snmp_trap_source_form_exposes_runner_and_listen_port(client):
    response = client.get("/sources/new", headers=_identity_headers())
    body = response.get_data(as_text=True)
    assert_output_contains(
        body,
        "SNMP Trap via Runner",
        purpose="Source administration must expose SNMP traps as a first-class Runner-backed Signal Source.",
    )
    assert_output_contains(
        body,
        "UDP listen port",
        purpose="An SNMP Trap Source must make its dedicated UDP receiver port explicit so multiple Sources on a Runner cannot be ambiguous.",
    )


def test_reactor_can_match_dotted_snmp_oid_varbind_keys(app):
    with app.app_context():
        package = _reaction_package()
        source = SignalSource(name="SNMP OID Source", source_type="snmp_trap", enabled=True, snmp_port=162)
        source.set_allowed_networks(["127.0.0.1/32"])
        db.session.add(source)
        db.session.flush()
        signal = Signal(
            source=source,
            external_signal_id="snmp-oid-1",
            signal_type="snmp_trap",
            host="switch01.example",
            sender_ip="127.0.0.1",
        )
        signal.set_fields({
            "snmp": {
                "trap_oid": "1.3.6.1.6.3.1.1.5.3",
                "varbinds": {"1.3.6.1.2.1.2.2.1.1.17": "17"},
            }
        })
        reactor = Reactor(name="SNMP OID Reactor", source=source, package=package, mode="observe", enabled=True)
        reactor.set_match({"all": [{
            "field": "fields.snmp.varbinds.1.3.6.1.2.1.2.2.1.1.17",
            "operator": "equals",
            "value": "17",
        }]})
        reactor.set_mappings({})
        db.session.add_all([signal, reactor])
        db.session.commit()

        assert reactor_matches(reactor, signal) is True


def test_matching_recovery_signal_suppresses_pending_reaction(app):
    from datetime import timedelta
    from app.services.reactions import suppress_pending_reactions, utcnow

    with app.app_context():
        package = _reaction_package()
        source = _source("Recovery Source")
        down = _signal(source)
        down.host = "router01.example"
        down.description = "Interface Tu25111: Link down"
        down.received_at = utcnow()

        up = Signal(
            source=source,
            external_signal_id="90002",
            signal_type="recovery",
            host="router01.example",
            severity="information",
            description="Interface Tu25111: Link up",
            sender_ip="127.0.0.1",
            received_at=down.received_at + timedelta(seconds=20),
        )
        up.set_fields({})
        reactor = Reactor(
            name="Link Recovery Reactor",
            source=source,
            package=package,
            mode="automatic",
            enabled=True,
            recovery_window_seconds=120,
        )
        reactor.set_match({"all": [{"field": "description", "operator": "contains", "value": "link down"}]})
        reactor.set_recovery_match({"all": [{"field": "description", "operator": "contains", "value": "link up"}]})
        reactor.set_mappings({"hostname": {"kind": "signal", "path": "host"}})
        reactor.set_recovery_correlation_inputs(["hostname"])
        db.session.add_all([up, reactor])
        db.session.flush()

        reaction = Reaction(
            signal=down,
            reactor=reactor,
            package=package,
            mode="automatic",
            status="pending",
            execute_after=down.received_at + timedelta(seconds=120),
        )
        reaction.set_resolved_inputs({"hostname": down.host})
        db.session.add(reaction)
        db.session.flush()

        assert suppress_pending_reactions(reactor, up) == [reaction]
        assert reaction.status == "suppressed"
        assert reaction.recovery_signal_id == up.id
        assert reaction.execute_after is None


def test_recovery_signal_does_not_suppress_different_host(app):
    from datetime import timedelta
    from app.services.reactions import suppress_pending_reactions, utcnow

    with app.app_context():
        package = _reaction_package()
        source = _source("Recovery Correlation Source")
        down = _signal(source)
        down.host = "router01.example"
        down.received_at = utcnow()
        up = Signal(
            source=source,
            external_signal_id="90003",
            signal_type="recovery",
            host="router02.example",
            description="Link up",
            sender_ip="127.0.0.1",
            received_at=down.received_at + timedelta(seconds=10),
        )
        up.set_fields({})
        reactor = Reactor(
            name="Recovery Correlation Reactor",
            source=source,
            package=package,
            mode="automatic",
            enabled=True,
            recovery_window_seconds=60,
        )
        reactor.set_recovery_match({"all": [{"field": "description", "operator": "contains", "value": "link up"}]})
        reactor.set_mappings({"hostname": {"kind": "signal", "path": "host"}})
        reactor.set_recovery_correlation_inputs(["hostname"])
        db.session.add_all([up, reactor])
        db.session.flush()

        reaction = Reaction(
            signal=down,
            reactor=reactor,
            package=package,
            mode="automatic",
            status="pending",
            execute_after=down.received_at + timedelta(seconds=60),
        )
        reaction.set_resolved_inputs({"hostname": down.host})
        db.session.add(reaction)
        db.session.flush()

        assert suppress_pending_reactions(reactor, up) == []
        assert reaction.status == "pending"


def test_reactors_list_uses_actions_menu_with_edit_and_delete(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Reactor Actions Source")
        reactor = Reactor(
            name="Reactor Actions",
            description="",
            source=source,
            package=package,
            mode="observe",
            enabled=True,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.commit()
        reactor_id = reactor.id

    response = client.get("/reactors", headers=_identity_headers())
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Actions" in body
    assert '/reactors/{}/edit'.format(reactor_id) in body
    assert '/reactors/{}/delete'.format(reactor_id) in body
    assert 'data-confirm="Delete Reactor &quot;Reactor Actions&quot;? This cannot be undone."' in body


def test_reactor_delete_removes_configuration_and_scoped_notification_rules(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Delete Reactor Source")
        reactor = Reactor(
            name="Delete Me",
            description="",
            source=source,
            package=package,
            mode="observe",
            enabled=False,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        target = NotificationTarget(
            name="Delete Reactor Target",
            description="",
            channel="syslog",
            enabled=True,
            host="127.0.0.1",
            port=514,
            syslog_protocol="udp",
        )
        db.session.add_all([reactor, target])
        db.session.flush()
        rule = NotificationRule(
            scope_type="reactor",
            scope_id=reactor.id,
            event_type="reaction.failed",
            target=target,
        )
        db.session.add(rule)
        db.session.commit()
        reactor_id = reactor.id

    response = client.post(
        "/reactors/{}/delete".format(reactor_id),
        headers=_identity_headers(),
        follow_redirects=True,
    )
    body = html.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    assert 'Reactor "Delete Me" deleted.' in body
    with app.app_context():
        assert db.session.get(Reactor, reactor_id) is None
        assert NotificationRule.query.filter_by(scope_type="reactor", scope_id=reactor_id).count() == 0
        audit = AuditLog.query.filter_by(action="reactor.delete", object_id=reactor_id).order_by(AuditLog.id.desc()).first()
        assert audit is not None
        assert audit.object_name == "Delete Me"


def test_reactor_delete_preserves_reaction_history_with_snapshotted_identity(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("History Reactor Source")
        signal = _signal(source)
        reactor = Reactor(
            name="History Reactor",
            description="",
            source=source,
            package=package,
            mode="observe",
            enabled=False,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.flush()
        reaction = Reaction(
            signal=signal,
            reactor=reactor,
            package=package,
            mode="observe",
            status="observed",
            message="Historical Reaction",
        )
        reaction.set_resolved_inputs({})
        db.session.add(reaction)
        db.session.commit()
        reactor_id = reactor.id
        reaction_id = reaction.id
        signal_id = signal.id
        package_name = package.name

    response = client.post(
        "/reactors/{}/delete".format(reactor_id),
        headers=_identity_headers(),
        follow_redirects=True,
    )
    body = html.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    assert 'Reactor "History Reactor" deleted.' in body
    with app.app_context():
        assert db.session.get(Reactor, reactor_id) is None
        preserved = db.session.get(Reaction, reaction_id)
        assert preserved is not None
        assert preserved.reactor_id is None
        assert preserved.reactor_name_snapshot == "History Reactor"
        assert preserved.source_name_snapshot == "History Reactor Source"
        assert preserved.package_name_snapshot == package_name
        assert preserved.reactor_display_name == "History Reactor (deleted)"

    for path in (
        "/reactions",
        "/reactions/{}".format(reaction_id),
        "/signals/{}".format(signal_id),
    ):
        rendered = client.get(path, headers=_identity_headers())
        assert rendered.status_code == 200
        rendered_body = rendered.get_data(as_text=True)
        assert "History Reactor (deleted)" in rendered_body
        assert "Historical Reaction" in rendered_body


def test_reactor_delete_refuses_pending_reaction_waiting_for_recovery_window(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Pending Reactor Source")
        signal = _signal(source)
        reactor = Reactor(
            name="Pending Reactor",
            description="",
            source=source,
            package=package,
            mode="automatic",
            enabled=False,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.flush()
        reaction = Reaction(
            signal=signal,
            reactor=reactor,
            package=package,
            reactor_name_snapshot=reactor.name,
            source_name_snapshot=source.name,
            package_name_snapshot=package.name,
            mode="automatic",
            status="pending",
            message="Waiting for recovery window.",
        )
        reaction.set_resolved_inputs({})
        db.session.add(reaction)
        db.session.commit()
        reactor_id = reactor.id
        reaction_id = reaction.id

    response = client.post(
        "/reactors/{}/delete".format(reactor_id),
        headers=_identity_headers(),
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'cannot be deleted while pending Reaction #{}'.format(reaction_id) in body
    with app.app_context():
        assert db.session.get(Reactor, reactor_id) is not None


def test_reactor_delete_requires_administrator(app, client):
    with app.app_context():
        package = _reaction_package()
        source = _source("Protected Reactor Source")
        reactor = Reactor(
            name="Protected Reactor",
            description="",
            source=source,
            package=package,
            mode="observe",
            enabled=False,
        )
        reactor.set_match({"all": []})
        reactor.set_mappings({})
        db.session.add(reactor)
        db.session.commit()
        reactor_id = reactor.id

    response = client.post(
        "/reactors/{}/delete".format(reactor_id),
        headers=_identity_headers("user1"),
    )
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Reactor, reactor_id) is not None
