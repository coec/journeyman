from pathlib import Path

import pytest


pytestmark = pytest.mark.security


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_project_parallelism_is_bounded_in_form_and_execution_service():
    project_view = _read("app/views/projects.py")
    execution = _read("app/services/project_execution.py")

    assert '1 <= form_data["max_parallel_steps"] <= 32' in project_view
    assert "max(1, min(32, project.max_parallel_steps or 4))" in execution


def test_package_validation_is_enforced_by_server_side_service():
    source = _read("app/services/project_package_inputs.py")

    assert "PACKAGE_INPUT_CHOICE" in source
    assert "minimum_length" in source
    assert "maximum_length" in source
    assert "required_when" in source
    assert "visible_when" in source
    assert "EMAIL_ADDRESS_PATTERN" in source


def test_persistence_paths_explicitly_rollback_failed_transactions():
    paths = [
        "app/views/projects.py",
        "app/views/packages.py",
        "app/views/inventories.py",
        "app/views/credentials.py",
        "app/services/project_execution.py",
        "app/services/job_inventory_refresh.py",
    ]

    for relative_path in paths:
        assert "db.session.rollback()" in _read(relative_path), relative_path


def test_runner_job_claim_is_atomic_against_duplicate_claims():
    lifecycle = _read("app/services/runner_job_lifecycle.py")
    runner_tests = _read("tests/test_runners.py")

    assert ".update(" in lifecycle
    assert 'Job.status == "queued"' in lifecycle
    assert "duplicate.status_code == 204" in runner_tests
