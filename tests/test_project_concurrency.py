"""Project concurrency policy."""

from types import SimpleNamespace

import pytest

from app import db
from app.models import Job, Project
from app.services.project_concurrency import (
    CONCURRENCY_DISTINCT,
    CONCURRENCY_EXCLUSIVE,
    CONCURRENCY_SERIALIZED,
    CONCURRENCY_UNRESTRICTED,
    job_can_start,
    launch_blocking_job,
    parameter_signature,
    project_concurrency_message,
    scoped_parameter_signature,
)
from app.services.project_execution import ProjectExecutionQueueError, queue_project_execution


def _project(*, concurrency_policy=CONCURRENCY_EXCLUSIVE):
    return Project(
        name="Concurrency Project",
        enabled=True,
        owner="admin",
        concurrency_policy=concurrency_policy,
    )


@pytest.mark.parametrize("status", ["queued", "running", "waiting_oversight", "cancelling"])
def test_exclusive_project_detects_active_jobs(app, status):
    with app.app_context():
        project = _project()
        blocker = Job(project=project, project_name=project.name, status=status, requested_by="alice")
        db.session.add(blocker)
        db.session.commit()
        found = launch_blocking_job(project, CONCURRENCY_EXCLUSIVE, "signature")
        assert found.id == blocker.id
        assert "uses Exclusive concurrency" in project_concurrency_message(project, CONCURRENCY_EXCLUSIVE, blocker)


@pytest.mark.parametrize("status", ["successful", "failed", "cancelled"])
def test_exclusive_project_ignores_terminal_jobs(app, status):
    with app.app_context():
        project = _project()
        db.session.add(Job(project=project, project_name=project.name, status=status, requested_by="alice"))
        db.session.commit()
        assert launch_blocking_job(project, CONCURRENCY_EXCLUSIVE, "signature") is None


def test_unrestricted_and_serialized_do_not_reject_launch(app):
    with app.app_context():
        project = _project(concurrency_policy=CONCURRENCY_SERIALIZED)
        db.session.add(Job(project=project, project_name=project.name, status="running", requested_by="alice"))
        db.session.commit()
        assert launch_blocking_job(project, CONCURRENCY_SERIALIZED, "signature") is None
        assert launch_blocking_job(project, CONCURRENCY_UNRESTRICTED, "signature") is None


def test_distinct_parameters_only_blocks_matching_signature(app):
    with app.app_context():
        project = _project(concurrency_policy=CONCURRENCY_DISTINCT)
        blocker = Job(
            project=project,
            project_name=project.name,
            status="running",
            requested_by="alice",
            concurrency_signature="same",
        )
        db.session.add(blocker)
        db.session.commit()
        assert launch_blocking_job(project, CONCURRENCY_DISTINCT, "same").id == blocker.id
        assert launch_blocking_job(project, CONCURRENCY_DISTINCT, "different") is None


def test_parameter_signature_is_stable_and_changes_with_inputs(app):
    with app.app_context():
        a = SimpleNamespace(
            execution_vars={"cluster": "A", "tablespace": "USERS", "secret": "hidden"},
            inventory_bindings={},
            step_limit="",
            machine_credential_override_id=None,
        )
        b = SimpleNamespace(**a.__dict__)
        c = SimpleNamespace(
            execution_vars={"cluster": "A", "tablespace": "INDEXES", "secret": "hidden"},
            inventory_bindings={},
            step_limit="",
            machine_credential_override_id=None,
        )
        assert parameter_signature(a) == parameter_signature(b)
        assert parameter_signature(a) != parameter_signature(c)
        assert "hidden" not in parameter_signature(a)


def test_failed_only_rerun_signature_includes_selected_hosts(app):
    with app.app_context():
        base = parameter_signature(None)
        all_hosts = scoped_parameter_signature(
            base, scope="failed", hosts=["host1", "host2"]
        )
        same_hosts = scoped_parameter_signature(
            base, scope="failed", hosts=["host2", "host1"]
        )
        different_hosts = scoped_parameter_signature(
            base, scope="failed", hosts=["host1"]
        )

        assert all_hosts == same_hosts
        assert all_hosts != different_hosts
        assert all_hosts != base


def test_serialized_jobs_start_fifo_and_wait_for_running_instance(app):
    with app.app_context():
        project = _project(concurrency_policy=CONCURRENCY_SERIALIZED)
        first = Job(
            project=project, project_name=project.name, status="queued", requested_by="alice",
            concurrency_policy=CONCURRENCY_SERIALIZED,
        )
        second = Job(
            project=project, project_name=project.name, status="queued", requested_by="bob",
            concurrency_policy=CONCURRENCY_SERIALIZED,
        )
        db.session.add_all([first, second])
        db.session.commit()
        assert job_can_start(first) is True
        assert job_can_start(second) is False
        first.status = "running"
        db.session.commit()
        assert job_can_start(second) is False
        first.status = "successful"
        db.session.commit()
        assert job_can_start(second) is True


def test_queue_project_execution_rejects_exclusive_second_instance(app):
    with app.app_context():
        project = _project()
        blocker = Job(project=project, project_name=project.name, status="queued", requested_by="alice")
        db.session.add(blocker)
        db.session.commit()
        with pytest.raises(ProjectExecutionQueueError, match=r"uses Exclusive concurrency"):
            queue_project_execution(project=project, requested_by="bob")


def test_rerun_honours_exclusive_policy(app, monkeypatch):
    from app.services import job_rerun
    with app.app_context():
        project = _project()
        db.session.add(project)
        db.session.commit()
        source = SimpleNamespace(
            id=41, status="failed", steps=[object()], project=project,
            concurrency_policy=CONCURRENCY_EXCLUSIVE, concurrency_signature="sig",
        )
        blocker = SimpleNamespace(id=42, status="running")
        monkeypatch.setattr(job_rerun, "locked_project", lambda value: value)
        monkeypatch.setattr(job_rerun, "launch_blocking_job", lambda *args, **kwargs: blocker)
        with pytest.raises(job_rerun.JobRerunError, match=r"Job #42"):
            job_rerun.rerun_job(source, requested_by="admin")


def test_project_form_defaults_to_exclusive_and_places_field_before_enabled(client):
    response = client.get("/projects/new", headers={"X-Test-Username": "admin"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<option value="exclusive" selected>' in html
    assert "Distinct parameters" in html
    assert "Serialized" in html
    assert "Exclusive" in html

    project_panel = html.split('id="project-main-panel"', 1)[1].split(
        'id="project-defaults-panel"', 1
    )[0]
    assert project_panel.index('name="concurrency_policy"') < project_panel.index('name="enabled"')


def test_project_edit_form_reflects_concurrency_policy(app, client):
    with app.app_context():
        project = _project(concurrency_policy=CONCURRENCY_SERIALIZED)
        db.session.add(project)
        db.session.commit()
        project_id = project.id
    response = client.get(f"/projects/{project_id}/edit", headers={"X-Test-Username": "admin"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<option value="serialized" selected>' in html
