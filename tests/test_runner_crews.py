from datetime import datetime, timezone

from app import db
from app.models import Runner, RunnerCrew
from app.services.runner_crews import select_crew_runner


def _runner(name, *, running=0, load1=0.0, load5=0.0, cpus=4):
    runner = Runner(
        name=name,
        hostname="{}.example.com".format(name),
        runner_uuid="{}-uuid".format(name),
        enabled=True,
        is_local=False,
        api_secret_digest="digest",
        max_concurrent_steps=4,
        running_steps=running,
        load_average_1m=load1,
        load_average_5m=load5,
        cpu_count=cpus,
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    runner.set_capabilities(["ansible", "shell"])
    db.session.add(runner)
    db.session.flush()
    return runner


def test_runner_crew_prefers_fewer_active_steps(app):
    with app.app_context():
        busy = _runner("busy", running=2, load1=0.1)
        idle = _runner("idle", running=0, load1=8.0)
        crew = RunnerCrew(name="Melbourne", runners=[busy, idle])
        db.session.add(crew)
        db.session.flush()

        assert select_crew_runner(crew, required_capabilities={"ansible"}) is idle


def test_runner_crew_uses_normalized_load_to_break_active_work_tie(app):
    with app.app_context():
        high = _runner("high", running=1, load1=8.0, load5=4.0, cpus=4)
        low = _runner("low", running=1, load1=4.0, load5=4.0, cpus=8)
        crew = RunnerCrew(name="Melbourne", runners=[high, low])
        db.session.add(crew)
        db.session.flush()

        assert select_crew_runner(crew, required_capabilities={"ansible"}) is low


def test_runner_crew_ignores_offline_and_incompatible_members(app):
    with app.app_context():
        offline = _runner("offline")
        offline.last_heartbeat_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        incompatible = _runner("incompatible")
        incompatible.set_capabilities(["shell"])
        selected = _runner("selected", running=3)
        crew = RunnerCrew(
            name="Melbourne",
            runners=[offline, incompatible, selected],
        )
        db.session.add(crew)
        db.session.flush()

        assert select_crew_runner(crew, required_capabilities={"ansible"}) is selected


def test_runner_crew_admin_page_can_create_membership(app, client):
    with app.app_context():
        one = _runner("kar-runner01")
        two = _runner("kar-runner02")
        db.session.commit()
        ids = [one.id, two.id]

    response = client.post(
        "/runner-crews/new",
        data={
            "name": "Melbourne",
            "description": "Melbourne execution runners",
            "runner_ids": [str(item) for item in ids],
        },
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Melbourne" in response.data

    with app.app_context():
        crew = RunnerCrew.query.filter_by(name="Melbourne").one()
        assert [runner.name for runner in crew.runners] == [
            "kar-runner01",
            "kar-runner02",
        ]
