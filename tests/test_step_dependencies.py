import json

from app.models import JobStep, ProjectStep


def test_project_step_dependency_positions_are_normalized():
    step = ProjectStep()
    step.set_dependency_positions([3, 1, 3, 2])
    assert step.get_dependency_positions() == [1, 2, 3]
    assert json.loads(step.depends_on_json) == [1, 2, 3]


def test_job_step_dependency_positions_are_normalized():
    step = JobStep()
    step.set_dependency_positions([2, 1, 2])
    assert step.get_dependency_positions() == [1, 2]


def test_invalid_dependency_json_is_safe():
    step = ProjectStep(depends_on_json="not-json")
    assert step.get_dependency_positions() == []
