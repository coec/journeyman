from datetime import datetime, timezone

from app import db
from app.models import Environment, Job, Project, Runner
from app.services.runner_draining import request_runner_drain
from app.services.runner_environment_sync import (
    claim_next_environment_sync,
    queue_environment_sync,
)
from app.services.runners import CURRENT_REMOTE_RUNNER_VERSION, runner_health


def _runner(name="drain-runner"):
    runner = Runner(
        name=name,
        hostname="{}.example.com".format(name),
        runner_uuid="{}-uuid".format(name),
        enabled=True,
        is_local=False,
        api_secret_digest="digest",
        max_concurrent_steps=4,
        running_steps=0,
        last_heartbeat_at=datetime.now(timezone.utc),
        version=CURRENT_REMOTE_RUNNER_VERSION,
    )
    runner.set_capabilities(["ansible", "shell"])
    db.session.add(runner)
    db.session.flush()
    return runner


def _job():
    project = Project(name="Drain test project", enabled=True)
    db.session.add(project)
    db.session.flush()
    job = Job(
        project_id=project.id,
        project_name=project.name,
        status="queued",
        requested_by="admin",
        dispatch_target="local",
    )
    db.session.add(job)
    db.session.flush()
    return job


def test_runner_drain_changes_health_and_releases_when_job_finishes(app):
    with app.app_context():
        runner = _runner()
        job = _job()

        assert runner_health(runner) == "healthy"
        assert request_runner_drain(runner, job, action="update") is True
        assert runner_health(runner) == "draining"
        assert runner.drain_job_id == job.id

        job.status = "cancelled"
        db.session.commit()

        db.session.refresh(runner)
        assert runner.drain_job_id is None
        assert runner.drain_requested_at is None
        assert runner.drain_reason == ""
        assert runner_health(runner) == "healthy"


def test_draining_runner_cannot_claim_environment_sync(app):
    with app.app_context():
        runner = _runner("drain-sync-runner")
        environment = Environment(
            name="Drain sync environment",
            path="/opt/journeyman/environments/drain-sync",
            enabled=True,
            is_managed=True,
            ansible_spec="ansible-core==2.21.3",
            python_version="Python 3.14.5",
            ansible_version="ansible-playbook [core 2.21.3]",
            validation_status="passed",
            build_status="passed",
        )
        db.session.add(environment)
        db.session.flush()
        sync = queue_environment_sync(environment, runner)
        owner = _job()
        request_runner_drain(runner, owner, action="update")
        db.session.commit()

        assert claim_next_environment_sync(runner) is None
        db.session.refresh(sync)
        assert sync.status == "queued"


def test_claim_next_job_skips_management_job_while_target_runner_is_busy(
    app,
    monkeypatch,
):
    """A draining management Job must stay queued without starving local work."""
    import importlib.util
    from pathlib import Path

    from app import db
    from app.models import Job

    runner_script = Path(app.root_path).parent / "bin" / "journeyman-runner"
    spec = importlib.util.spec_from_file_location(
        "journeyman_runner_test_module",
        runner_script,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with app.app_context():
        management = Job(
            status="queued",
            dispatch_target="local",
            message="",
        )
        ordinary = Job(
            status="queued",
            dispatch_target="local",
            message="",
        )
        db.session.add_all([management, ordinary])
        db.session.commit()

        monkeypatch.setattr(
            "app.services.project_concurrency.job_can_start",
            lambda job: True,
        )
        monkeypatch.setattr(
            "app.services.runner_draining.management_job_ready_to_start",
            lambda job: job.id != management.id,
        )

        claimed = module.claim_next_job()

        assert claimed.id == ordinary.id
        assert db.session.get(Job, management.id).status == "queued"
        assert db.session.get(Job, ordinary.id).status == "running"
