from app import db
from app.models import Project, Repository


def test_project_edit_keeps_existing_default_repository(client, app):
    with app.app_context():
        repository = Repository(
            name="SysAdmin",
            url="https://git.example.test/sysadmin.git",
            default_branch="main",
            status="never_synced",
        )
        db.session.add(repository)
        db.session.flush()

        project = Project(
            name="Existing Project",
            description="Regression test",
            execution_type="ansible",
            repository_id=repository.id,
            max_parallel_steps=4,
            runner_routing="local",
            runner_site="",
            enabled=True,
            owner="admin",
        )
        db.session.add(project)
        db.session.commit()

        project_id = project.id
        repository_id = repository.id

    response = client.get(
        "/projects/{}/edit".format(project_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200

    html = response.data.decode("utf-8")

    assert "SysAdmin" in html
    assert (
        '<option value="{}" selected>'.format(repository_id)
        in html
    )
