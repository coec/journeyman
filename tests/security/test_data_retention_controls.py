"""ASVS evidence for protected-data retention and cache controls."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.models import Job, Project, ProjectPackage, Reaction, Reactor, Signal, SignalSource
from app.services.data_retention import purge_expired_jobs, purge_expired_reactions
from app.services.inventory_cache import purge_expired_inventory_caches

pytestmark = pytest.mark.security


def test_authenticated_response_is_never_cacheable(client):
    response = client.get("/", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_completed_jobs_older_than_retention_are_purged(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        app.config["JOB_RETENTION_DAYS"] = 30
        project = Project(name="Retention project", owner="admin")
        old = Job(
            project=project,
            project_name=project.name,
            status="successful",
            requested_by="admin",
            finished_at=now - timedelta(days=31),
        )
        recent = Job(
            project=project,
            project_name=project.name,
            status="successful",
            requested_by="admin",
            finished_at=now - timedelta(days=29),
        )
        running = Job(
            project=project,
            project_name=project.name,
            status="running",
            requested_by="admin",
            finished_at=now - timedelta(days=90),
        )
        db.session.add_all([project, old, recent, running])
        db.session.commit()
        old_id, recent_id, running_id = old.id, recent.id, running.id

        assert purge_expired_jobs(now=now) == [old_id]
        assert db.session.get(Job, old_id) is None
        assert db.session.get(Job, recent_id) is not None
        assert db.session.get(Job, running_id) is not None


def test_inventory_cache_retention_removes_only_expired_json(
    app,
    monkeypatch,
    tmp_path,
):
    now = datetime.now(timezone.utc)
    with app.app_context():
        from app.services import inventory_cache

        cache_dir = str(tmp_path / "inventory_cache")
        monkeypatch.setattr(
            inventory_cache,
            "_cache_directory",
            lambda: cache_dir,
        )
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        old = os.path.join(cache_dir, "1.json")
        recent = os.path.join(cache_dir, "2.json")
        ignored = os.path.join(cache_dir, "notes.txt")
        for path in (old, recent, ignored):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        os.utime(old, (now.timestamp() - 7200, now.timestamp() - 7200))
        os.utime(recent, (now.timestamp(), now.timestamp()))
        os.utime(ignored, (now.timestamp() - 7200, now.timestamp() - 7200))

        removed = purge_expired_inventory_caches(max_age_seconds=3600, now=now)
        assert removed == [old]
        assert not os.path.exists(old)
        assert os.path.exists(recent)
        assert os.path.exists(ignored)


def test_terminal_reactions_older_than_retention_are_purged(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        app.config["REACTION_RETENTION_DAYS"] = 30
        project = Project(name="Reaction retention project", owner="admin")
        package = ProjectPackage(
            name="Reaction retention package",
            project=project,
            owner="admin",
        )
        source = SignalSource(
            name="Reaction retention source",
            source_type="syslog",
            enabled=True,
        )
        reactor = Reactor(
            name="Reaction retention reactor",
            source=source,
            package=package,
            mode="observe",
            enabled=True,
        )
        old_signal = Signal(
            source=source,
            external_signal_id="old-retention-signal",
            received_at=now - timedelta(days=31),
        )
        recent_signal = Signal(
            source=source,
            external_signal_id="recent-retention-signal",
            received_at=now - timedelta(days=29),
        )
        pending_signal = Signal(
            source=source,
            external_signal_id="pending-retention-signal",
            received_at=now - timedelta(days=90),
        )
        old = Reaction(
            signal=old_signal,
            reactor=reactor,
            package=package,
            mode="observe",
            status="observed",
            created_at=now - timedelta(days=31),
        )
        recent = Reaction(
            signal=recent_signal,
            reactor=reactor,
            package=package,
            mode="observe",
            status="observed",
            created_at=now - timedelta(days=29),
        )
        pending = Reaction(
            signal=pending_signal,
            reactor=reactor,
            package=package,
            mode="automatic",
            status="pending",
            created_at=now - timedelta(days=90),
        )
        db.session.add_all([
            project,
            package,
            source,
            reactor,
            old_signal,
            recent_signal,
            pending_signal,
            old,
            recent,
            pending,
        ])
        db.session.commit()
        old_id, recent_id, pending_id = old.id, recent.id, pending.id

        assert purge_expired_reactions(now=now) == [old_id]
        assert db.session.get(Reaction, old_id) is None
        assert db.session.get(Reaction, recent_id) is not None
        assert db.session.get(Reaction, pending_id) is not None
