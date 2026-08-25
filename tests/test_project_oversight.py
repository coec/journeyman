from types import SimpleNamespace

from app.services.project_oversight import dependency_state, oversight_candidates


def _step(position, status="pending", dependencies=None, failure_only=False, approved=False):
    return SimpleNamespace(
        position=position,
        status=status,
        failure_only=failure_only,
        oversight_required_before=bool(dependencies),
        oversight_approved=approved,
        get_dependency_positions=lambda: list(dependencies or []),
    )


def test_oversight_project_form_control(client):
    response = client.get(
        "/projects/new",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert 'id="project-oversight-all"' in html
    assert "Oversight required between all steps" in html
    assert "Oversight after this step" in html


def test_dependency_state_supports_success_and_failure_branches():
    source = _step(1, status="failed")
    success = _step(2, dependencies=[1])
    failure = _step(3, dependencies=[1], failure_only=True)
    mapping = {1: source, 2: success, 3: failure}

    assert dependency_state(success, mapping) == "blocked"
    assert dependency_state(failure, mapping) == "eligible"


def test_oversight_candidates_include_all_runnable_unapproved_branches():
    source = _step(1, status="successful", approved=True)
    branch_a = _step(2, dependencies=[1])
    branch_b = _step(3, dependencies=[1])
    later = _step(4, dependencies=[2, 3])
    job = SimpleNamespace(
        oversight_required_between_all_steps=True,
        steps=[source, branch_a, branch_b, later],
    )

    assert oversight_candidates(job) == [branch_a, branch_b]


def test_oversight_candidates_ignore_approved_batch():
    source = _step(1, status="successful", approved=True)
    next_step = _step(2, dependencies=[1], approved=True)
    job = SimpleNamespace(
        oversight_required_between_all_steps=True,
        steps=[source, next_step],
    )

    assert oversight_candidates(job) == []
