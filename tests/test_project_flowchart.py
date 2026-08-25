from types import SimpleNamespace

from app.services.project_flowchart import (
    build_project_flowchart,
)


def _step(
    position,
    name,
    dependencies=None,
    failure_only=False,
):
    return SimpleNamespace(
        position=position,
        name=name,
        playbook="step-{}.yml".format(position),
        enabled=True,
        failure_only=failure_only,
        refresh_inventory_after=False,
        inventory=None,
        inventory_id=None,
        get_dependency_positions=lambda: list(
            dependencies or []
        ),
    )


def test_flowchart_marks_success_and_failure_edges():
    project = SimpleNamespace(
        id=7,
        name="Branching Project",
        execution_type="ansible",
        inventory=SimpleNamespace(
            name="Default inventory"
        ),
        steps=[
            _step(1, "Validate"),
            _step(
                2,
                "Continue",
                [1],
                False,
            ),
            _step(
                3,
                "Rollback",
                [1],
                True,
            ),
        ],
    )

    graph = build_project_flowchart(project)

    assert graph["project_name"] == "Branching Project"
    assert graph["edges"] == [
        {
            "from": 1,
            "to": 2,
            "condition": "success",
        },
        {
            "from": 1,
            "to": 3,
            "condition": "failure",
        },
    ]

    rollback = next(
        node
        for node in graph["nodes"]
        if node["position"] == 3
    )

    assert rollback["failure_only"] is True


def test_flowchart_preserves_parallel_dependencies():
    project = SimpleNamespace(
        id=8,
        name="Parallel Project",
        execution_type="ansible",
        inventory=None,
        steps=[
            _step(1, "Start"),
            _step(2, "Branch A", [1]),
            _step(3, "Branch B", [1]),
            _step(4, "Join", [2, 3]),
        ],
    )

    graph = build_project_flowchart(project)

    assert {
        (edge["from"], edge["to"])
        for edge in graph["edges"]
    } == {
        (1, 2),
        (1, 3),
        (2, 4),
        (3, 4),
    }


def test_projects_template_contains_flowchart_modal():
    from pathlib import Path

    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "projects.html"
    ).read_text(encoding="utf-8")

    assert "project-flowchart-open" in template
    assert "Project Flowchart" in template
    assert "Result?" in template
    assert 'edge.condition === "failure"' in template
