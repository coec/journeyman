"""Cross-cutting evidence for ASVS V15 Secure Coding and Architecture."""

from pathlib import Path

import pytest

from app.config import ProductionConfig


pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_resource_demanding_execution_has_server_side_parallel_limits():
    project_view = _source("app/views/projects.py")
    project_execution = _source("app/services/project_execution.py")
    runner_dispatch = _source("app/services/runner_dispatch.py")

    assert "1 <= form_data[\"max_parallel_steps\"] <= 32" in project_view
    assert "max(1, min(32, project.max_parallel_steps or 4))" in project_execution
    assert "runner.running_steps >= runner.max_concurrent_steps" in runner_dispatch


def test_remote_job_claim_uses_conditional_database_update():
    source = _source("app/services/runner_dispatch.py")

    assert "Job.status == \"queued\"" in source
    assert "Job.assigned_runner_id.is_(None)" in source
    assert ".update(" in source
    assert "if updated != 1:" in source


def test_production_proxy_trust_is_explicit_and_bounded():
    assert ProductionConfig.PROXY_FIX_X_FOR == 1
    assert ProductionConfig.PROXY_FIX_X_PROTO == 1
    assert ProductionConfig.PROXY_FIX_X_HOST == 1
    assert ProductionConfig.PROXY_FIX_X_PORT == 1

    source = _source("app/__init__.py")
    assert "ProxyFix(" in source
    assert "request.headers.get(\"X-Forwarded-For\")" not in source
    assert "request.headers.get('X-Forwarded-For')" not in source


def test_request_data_is_not_bulk_mass_assigned_to_models():
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        risky_fragments = (
            "**request.form",
            "**request.args",
            "request.form.to_dict()",
            "request.args.to_dict()",
        )
        for fragment in risky_fragments:
            if fragment in text:
                offenders.append("{}: {}".format(path.relative_to(ROOT), fragment))

    assert offenders == []


def test_runner_assignment_manifest_is_an_explicit_field_subset():
    source = _source("app/services/runner_dispatch.py")

    assert '"dispatch_token": token' in source
    assert '"steps": [' in source
    assert "job.__dict__" not in source
    assert "step.__dict__" not in source
    assert "vars(job)" not in source
    assert "vars(step)" not in source


def test_production_configuration_does_not_enable_debug_functionality():
    assert ProductionConfig.DEBUG is False
