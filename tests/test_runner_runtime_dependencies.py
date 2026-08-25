import json
from datetime import datetime, timezone

from app import db
from app.models import Runner
from app.services import runner_runtime_dependencies as runtime


def test_runner_runtime_roots_include_only_agent_import_dependencies():
    assert runtime.RUNNER_RUNTIME_ROOT_PACKAGES == ("cryptography",)
    assert "ansible-core" not in runtime.RUNNER_RUNTIME_ROOT_PACKAGES


def test_runtime_dependency_state_detects_drift_without_environment_packages(app, monkeypatch):
    expected = {
        "ansible-core": "2.21.3",
        "cryptography": "50.0.0",
        "packaging": "26.2",
    }
    monkeypatch.setattr(runtime, "canonical_runner_runtime_dependencies", lambda: expected)

    with app.app_context():
        runner = Runner(name="runtime-drift", hostname="runner01", enabled=True)
        runner.runtime_dependencies_json = json.dumps({
            "ansible-core": "2.21.3",
            "cryptography": "49.0.0",
            "packaging": "26.2",
            # An arbitrary Environment-only package is ignored by state
            # comparison because it is outside the canonical runtime closure.
            "netaddr": "1.3.0",
        })
        db.session.add(runner)
        db.session.commit()

        state = runtime.runner_runtime_dependency_state(runner)
        assert state["state"] == "drifted"
        assert state["drift"] == [{
            "name": "cryptography",
            "expected": "50.0.0",
            "reported": "49.0.0",
        }]


def test_reported_runtime_dependency_change_invalidates_previous_audit(app, monkeypatch):
    monkeypatch.setattr(
        runtime,
        "runner_runtime_dependency_names_for_reporting",
        lambda: ["cryptography"],
    )

    with app.app_context():
        runner = Runner(name="audit-reset", hostname="runner02", enabled=True)
        runner.runtime_dependencies_json = json.dumps({"ansible-core": "2.21.2"})
        runner.runtime_dependency_audit_status = "clean"
        runner.runtime_dependency_audit_fingerprint = "old"
        runner.runtime_dependency_audit_checked_at = datetime.now(timezone.utc)
        db.session.add(runner)
        db.session.commit()

        runtime.set_reported_runner_runtime_dependencies(
            runner,
            {"cryptography": "50.0.0", "ignored": "1"},
        )

        assert json.loads(runner.runtime_dependencies_json) == {
            "cryptography": "50.0.0",
        }
        assert runner.runtime_dependency_audit_status == "pending"
        assert runner.runtime_dependency_audit_fingerprint == ""
        assert runner.runtime_dependency_audit_checked_at is None


def test_runtime_audit_result_is_reused_for_matching_runner_fingerprints(app, monkeypatch):
    expected = {"cryptography": "50.0.0"}
    monkeypatch.setattr(runtime, "canonical_runner_runtime_dependencies", lambda: expected)
    calls = []

    def fake_audit(dependencies):
        calls.append(dict(dependencies))
        return {
            "status": "clean",
            "message": "clean",
            "details": {"finding_count": 0, "findings": []},
        }

    monkeypatch.setattr(runtime, "_audit_exact_dependencies", fake_audit)

    with app.app_context():
        for name in ("runner-a", "runner-b"):
            runner = Runner(name=name, hostname=name, enabled=True)
            runner.runtime_dependencies_json = json.dumps(expected)
            db.session.add(runner)
        db.session.commit()

        result = runtime.refresh_runner_runtime_dependency_audits(force=True)

        assert len(calls) == 1
        assert result["clean"] >= 2
        rows = Runner.query.filter(Runner.name.in_(["runner-a", "runner-b"])).all()
        assert {row.runtime_dependency_audit_status for row in rows} == {"clean"}
