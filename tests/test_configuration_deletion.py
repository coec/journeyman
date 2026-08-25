from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db
from app.models import Job, JobPackageSnapshot, Project, ProjectPackage, Runner
from app.services.configuration_deletion import (
    ConfigurationDeletionError,
    delete_package_with_job_history,
    delete_project_with_job_history,
)


def _job(project, *, status="successful", name=None):
    return Job(
        project=project,
        project_name=name or project.name,
        requested_by="admin",
        status=status,
    )


def _package_snapshot(job, package):
    return JobPackageSnapshot(
        job=job,
        package=package,
        package_name=package.name,
        package_owner=package.owner,
        package_definition_json="{}",
        package_definition_sha256="0" * 64,
        display_values_json="[]",
        operational_targets_json="[]",
        inventory_bindings_json="{}",
        encrypted_extra_vars=b"test-only",
        step_limit="",
    )


def _create_output_roots(app, monkeypatch, tmp_path, job_id):
    inventory_root = tmp_path / "inventory-snapshots"
    job_root = tmp_path / "jobs"
    artifact_root = tmp_path / "runner-artifacts"
    monkeypatch.setenv("JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT", str(inventory_root))
    monkeypatch.setenv("JOURNEYMAN_JOB_ROOT", str(job_root))
    app.config["RUNNER_ARTIFACT_ROOT"] = artifact_root

    paths = []
    for root in (inventory_root, job_root, artifact_root):
        directory = root / str(job_id)
        directory.mkdir(parents=True)
        (directory / "output.txt").write_text("job output\n", encoding="utf-8")
        paths.append(directory)
    return paths


def test_project_delete_cascades_terminal_jobs_and_filesystem_output(app, monkeypatch, tmp_path):
    with app.app_context():
        project = Project(name="Old Project", owner="admin")
        job = _job(project)
        db.session.add_all([project, job])
        db.session.commit()
        project_id, job_id = project.id, job.id
        paths = _create_output_roots(app, monkeypatch, tmp_path, job_id)

        deleted_ids, cleanup_errors = delete_project_with_job_history(project)

        assert deleted_ids == [job_id]
        assert cleanup_errors == []
        assert db.session.get(Project, project_id) is None
        assert db.session.get(Job, job_id) is None
        assert all(not path.exists() for path in paths)


def test_project_delete_refuses_active_jobs(app):
    with app.app_context():
        project = Project(name="Busy Project", owner="admin")
        job = _job(project, status="running")
        db.session.add_all([project, job])
        db.session.commit()

        with pytest.raises(ConfigurationDeletionError, match="associated Jobs are active"):
            delete_project_with_job_history(project)

        assert db.session.get(Project, project.id) is not None
        assert db.session.get(Job, job.id) is not None


def test_project_delete_still_requires_packages_to_be_removed_first(app):
    with app.app_context():
        project = Project(name="Packaged Project", owner="admin")
        package = ProjectPackage(name="Still Here", project=project, owner="admin")
        db.session.add_all([project, package])
        db.session.commit()

        with pytest.raises(ConfigurationDeletionError, match="one or more Packages"):
            delete_project_with_job_history(project)


def test_package_delete_removes_only_jobs_launched_through_that_package(app, monkeypatch, tmp_path):
    with app.app_context():
        project = Project(name="Package Project", owner="admin")
        package = ProjectPackage(name="Old Package", project=project, owner="admin")
        packaged_job = _job(project)
        direct_job = _job(project)
        packaged_job.package_snapshot = _package_snapshot(packaged_job, package)
        db.session.add_all([project, package, packaged_job, direct_job])
        db.session.commit()
        package_id = package.id
        packaged_job_id = packaged_job.id
        direct_job_id = direct_job.id
        paths = _create_output_roots(app, monkeypatch, tmp_path, packaged_job_id)

        deleted_ids, cleanup_errors = delete_package_with_job_history(package)

        assert deleted_ids == [packaged_job_id]
        assert cleanup_errors == []
        assert db.session.get(ProjectPackage, package_id) is None
        assert db.session.get(Job, packaged_job_id) is None
        assert db.session.get(Job, direct_job_id) is not None
        assert all(not path.exists() for path in paths)


def test_package_delete_refuses_active_package_job(app):
    with app.app_context():
        project = Project(name="Active Package Project", owner="admin")
        package = ProjectPackage(name="Active Package", project=project, owner="admin")
        job = _job(project, status="queued")
        job.package_snapshot = _package_snapshot(job, package)
        db.session.add_all([project, package, job])
        db.session.commit()

        with pytest.raises(ConfigurationDeletionError, match="associated Jobs are active"):
            delete_package_with_job_history(package)

        assert db.session.get(ProjectPackage, package.id) is not None
        assert db.session.get(Job, job.id) is not None


def test_delete_confirmation_warns_that_job_history_is_cascaded():
    root = Path(__file__).resolve().parents[1]
    projects = (root / "app" / "templates" / "projects.html").read_text()
    packages = (root / "app" / "templates" / "project_packages.html").read_text()

    assert "all associated Job history/output" in projects
    assert "all Jobs launched through it, including their output" in packages


def test_project_delete_recovers_stale_cancelling_job_when_runner_is_idle(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        local_runner = Runner(
            name="test local runner",
            runner_uuid="local:test",
            hostname="test",
            site="local",
            is_local=True,
            enabled=True,
            max_concurrent_steps=1,
            running_steps=0,
            last_heartbeat_at=now,
        )
        project = Project(name="Stale Cancel Project", owner="admin")
        job = _job(project, status="cancelling")
        job.dispatch_target = "local"
        job.started_at = now - timedelta(hours=2)
        job.cancel_requested_at = now - timedelta(minutes=10)
        db.session.add_all([local_runner, project, job])
        db.session.commit()
        job_id = job.id

        deleted_ids, cleanup_errors = delete_project_with_job_history(project)

        assert deleted_ids == [job_id]
        assert cleanup_errors == []
        assert db.session.get(Project, project.id) is None
        assert db.session.get(Job, job_id) is None


def test_project_delete_still_refuses_stale_cancelling_job_when_runner_is_busy(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        local_runner = Runner(
            name="busy local runner",
            runner_uuid="local:busy",
            hostname="busy",
            site="local",
            is_local=True,
            enabled=True,
            max_concurrent_steps=1,
            running_steps=1,
            last_heartbeat_at=now,
        )
        project = Project(name="Possibly Live Cancel Project", owner="admin")
        job = _job(project, status="cancelling")
        job.dispatch_target = "local"
        job.started_at = now - timedelta(hours=2)
        job.cancel_requested_at = now - timedelta(minutes=10)
        db.session.add_all([local_runner, project, job])
        db.session.commit()

        with pytest.raises(ConfigurationDeletionError, match="associated Jobs are active"):
            delete_project_with_job_history(project)

        assert db.session.get(Project, project.id) is not None
        assert db.session.get(Job, job.id).status == "cancelling"
