from datetime import datetime, timedelta, timezone

from app import db
from app.models import Job, Project, Runner
from app.services.job_cancellation import cancel_job, recover_stale_cancelling_jobs


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_running_job_cancellation_records_request_time(app):
    with app.app_context():
        project = Project(name="Cancel Timestamp", owner="admin")
        job = Job(
            project=project, project_name=project.name, requested_by="admin",
            status="running", dispatch_target="local",
        )
        db.session.add_all([project, job])
        db.session.commit()

        result = cancel_job(job)

        assert result.status == "cancelling"
        assert job.cancel_requested_at is not None


def test_stale_cancellation_recovers_only_when_runner_reports_idle(app):
    with app.app_context():
        now = _now_naive()
        runner = Runner(
            name="local test", runner_uuid="local:test-recovery", hostname="test",
            site="local", enabled=True, is_local=True, max_concurrent_steps=1,
            running_steps=1, last_heartbeat_at=now,
        )
        project = Project(name="Recovery", owner="admin")
        job = Job(
            project=project, project_name=project.name, requested_by="admin",
            status="cancelling", dispatch_target="local", started_at=now - timedelta(hours=1),
            cancel_requested_at=now - timedelta(minutes=10),
        )
        db.session.add_all([runner, project, job])
        db.session.commit()

        assert recover_stale_cancelling_jobs(now=now, stale_seconds=60) == []
        assert job.status == "cancelling"

        runner.running_steps = 0
        db.session.commit()
        recovered = recover_stale_cancelling_jobs(now=now, stale_seconds=60)

        assert recovered == [job.id]
        assert job.status == "cancelled"
        assert job.finished_at is not None
