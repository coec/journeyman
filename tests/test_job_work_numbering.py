"""Shared numbering for Jobs and Environment synchronization work."""

from app import db
from app.models import Environment, Job, Project, Runner, RunnerEnvironmentSync


def test_job_and_environment_sync_share_one_number_sequence(app):
    with app.app_context():
        project = Project(name="Numbered Project", enabled=True, owner="admin")
        runner = Runner(
            name="numbered-runner",
            hostname="numbered-runner.example.com",
            enabled=True,
            is_local=False,
        )
        environment = Environment(
            name="Numbered Environment",
            path="/opt/journeyman/environments/numbered",
            enabled=True,
            is_managed=True,
        )
        db.session.add_all([project, runner, environment])
        db.session.flush()

        first = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="alice",
        )
        db.session.add(first)
        db.session.flush()

        sync = RunnerEnvironmentSync(
            runner=runner,
            environment=environment,
            status="queued",
            requested_by="alice",
        )
        db.session.add(sync)
        db.session.flush()

        second = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="alice",
        )
        db.session.add(second)
        db.session.flush()

        assert first.id + 1 == sync.work_item_id
        assert sync.work_item_id + 1 == second.id


def test_jobs_page_uses_shared_number_for_environment_sync(client, app):
    with app.app_context():
        runner = Runner(
            name="jobs-number-runner",
            hostname="jobs-number-runner.example.com",
            enabled=True,
            is_local=False,
        )
        environment = Environment(
            name="Jobs number environment",
            path="/opt/journeyman/environments/jobs-number",
            enabled=True,
            is_managed=True,
        )
        db.session.add_all([runner, environment])
        db.session.flush()
        sync = RunnerEnvironmentSync(
            runner=runner,
            environment=environment,
            status="queued",
            requested_by="alice",
        )
        db.session.add(sync)
        db.session.commit()
        expected = "#{}".format(sync.work_item_id)

    response = client.get("/jobs", headers={"X-Test-Username": "alice"})
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert expected in html
    assert "Sync #" not in html
