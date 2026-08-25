from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


def _runner():
    return run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "bin"
            / "journeyman-runner"
        )
    )


def _step(position, status, dependencies=None, failure_only=False):
    return SimpleNamespace(
        position=position,
        status=status,
        failure_only=failure_only,
        get_dependency_positions=lambda: list(dependencies or []),
    )


def test_failure_branch_detection():
    runner = _runner()
    source = _step(1, "failed")
    recovery = _step(2, "pending", [1], True)

    assert runner["has_failure_branch"](
        source,
        [source, recovery],
    ) is True


def test_project_form_contains_failure_only_control(client):
    response = client.get(
        "/projects/new",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert 'name="step_failure_only"' in html
    assert "On failure only" in html
    assert "Continue workflow" in html



def test_failure_only_branch_not_taken_is_skipped(app):
    runner = _runner()

    source = _step(
        1,
        "successful",
    )
    recovery = _step(
        2,
        "pending",
        [1],
        True,
    )
    job = SimpleNamespace(
        steps=[
            source,
            recovery,
        ],
    )

    with app.app_context():
        eligible, _ = runner[
            "select_eligible_steps"
        ](
            job,
            4,
        )

    assert eligible == []
    assert recovery.status == "skipped"


def test_success_branch_after_handled_failure_is_skipped(app):
    runner = _runner()

    source = _step(
        1,
        "failed",
    )
    success_path = _step(
        2,
        "pending",
        [1],
        False,
    )
    recovery = _step(
        3,
        "pending",
        [1],
        True,
    )
    job = SimpleNamespace(
        steps=[
            source,
            success_path,
            recovery,
        ],
    )

    with app.app_context():
        eligible, _ = runner[
            "select_eligible_steps"
        ](
            job,
            4,
        )

    assert recovery in eligible
    assert success_path.status == "skipped"
