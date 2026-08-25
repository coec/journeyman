from pathlib import Path

from app import db
from app.models import Project, ProjectStep


def _source_project():
    project = Project(
        name="Clone source",
        description="A source Project",
        enabled=True,
        execution_type="ansible",
        max_parallel_steps=3,
        concurrency_policy="exclusive",
        oversight_required_between_all_steps=True,
        runner_routing="local",
        runner_site="",
        owner="original.owner",
        security_scope="private",
    )
    project.steps = [
        ProjectStep(
            position=1,
            name="Validate",
            playbook="validate.yml",
            enabled=True,
            continue_on_failure=False,
            failure_only=False,
            refresh_repository=True,
            refresh_inventory_after=False,
            oversight_after=True,
            credentials_override=False,
            depends_on_json="[]",
        ),
        ProjectStep(
            position=2,
            name="Recover",
            playbook="recover.yml",
            enabled=True,
            continue_on_failure=False,
            failure_only=True,
            refresh_repository=False,
            refresh_inventory_after=True,
            credentials_override=False,
            depends_on_json="[1]",
        ),
    ]
    return project


def test_clone_project_copies_workflow(app, client):
    with app.app_context():
        source = _source_project()
        db.session.add(source)
        db.session.commit()
        source_id = source.id

    response = client.post(
        "/projects/{}/clone".format(source_id),
        headers={"X-Test-Username": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        cloned = Project.query.filter(Project.id != source_id).one()
        assert cloned.name == "Clone source (copy)"
        assert cloned.owner == "admin"
        assert cloned.description == "A source Project"
        assert cloned.max_parallel_steps == 3
        assert cloned.concurrency_policy == "exclusive"
        assert cloned.oversight_required_between_all_steps is True
        assert len(cloned.steps) == 2
        assert cloned.steps[0].refresh_repository is True
        assert cloned.steps[0].oversight_after is True
        assert cloned.steps[1].failure_only is True
        assert cloned.steps[1].refresh_inventory_after is True
        assert cloned.steps[1].get_dependency_positions() == [1]


def test_clone_project_name_is_unique(app, client):
    with app.app_context():
        source = _source_project()
        db.session.add(source)
        db.session.add(
            Project(
                name="Clone source (copy)",
                description="",
                enabled=True,
                execution_type="ansible",
                max_parallel_steps=4,
                runner_routing="local",
                runner_site="",
                owner="admin",
                security_scope="private",
            )
        )
        db.session.commit()
        source_id = source.id

    response = client.post(
        "/projects/{}/clone".format(source_id),
        headers={"X-Test-Username": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        assert (
            Project.query
            .filter(Project.name == "Clone source (copy 2)")
            .count()
            == 1
        )


def test_non_admin_cannot_clone_project(app, client):
    with app.app_context():
        source = _source_project()
        db.session.add(source)
        db.session.commit()
        source_id = source.id

    response = client.post(
        "/projects/{}/clone".format(source_id),
        headers={"X-Test-Username": "ordinary.user"},
    )
    assert response.status_code == 403


def test_project_form_has_clone_step_controls():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "project_form.html"
    ).read_text(encoding="utf-8")

    assert "clone-step" in template
    assert "captureDependencyReferences" in template
    assert "restoreDependencyReferences" in template
