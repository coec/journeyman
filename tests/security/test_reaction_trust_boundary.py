from app import db
from app.models import Project, ProjectPackage, Reactor, SignalSource
from tests.checks import assert_output_contains, assert_output_equal


def test_reactor_cannot_select_package_without_reaction_opt_in(app, client):
    with app.app_context():
        project = Project(name="Trust Project", description="", enabled=True, owner="admin", security_scope="private")
        package = ProjectPackage(name="Human Only Package", description="", project=project, enabled=True, allow_as_reaction=False, owner="admin", access_mode="restricted")
        package.set_fixed_vars({})
        source = SignalSource(name="Trust Source", source_type="syslog", enabled=True)
        source.set_allowed_networks(["10.0.0.1/32"])
        db.session.add_all([project, package, source])
        db.session.commit()
        source_id = source.id
        package_id = package.id

    response = client.post(
        "/reactors/new",
        data={
            "name": "Forbidden Reactor",
            "source_id": str(source_id),
            "package_id": str(package_id),
            "mode": "observe",
            "enabled": "on",
            "match_mode": "all",
            "cooldown_seconds": "0",
            "max_concurrency": "1",
        },
        headers={"X-Test-Username": "admin"},
    )
    body = response.get_data(as_text=True)
    assert_output_contains(
        body,
        "Select a Package with Allow as Reaction enabled.",
        purpose="A Reactor must never turn an ordinary human-launch Package into an automatically invokable Reaction target.",
    )
    with app.app_context():
        assert_output_equal(
            Reactor.query.count(),
            0,
            purpose="Rejecting a non-opted-in Reaction Package must leave no Reactor object behind.",
        )


def test_jxf_forbids_allow_as_reaction_field():
    from app.services.config_portability import _scan_forbidden_jxf_fields
    errors = _scan_forbidden_jxf_fields({"packages": [{"name": "Injected", "allow_as_reaction": True}]})
    assert_output_equal(
        bool(errors),
        True,
        purpose="Portable configuration must not be able to grant Reaction capability to an imported Package.",
    )
