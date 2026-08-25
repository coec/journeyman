import pytest

from app import db
from app.models import (
    Job,
    JobInventorySnapshot,
    JobRepositorySnapshot,
    JobStep,
    JobStepExecutionSlice,
    JobStepHostResult,
    Project,
    Repository,
    Environment,
    Runner,
    RunnerEnvironment,
)
from app.services.job_inventory_snapshot import (
    read_job_inventory_snapshot_data,
    write_job_inventory_snapshot,
)
from app.services.job_rerun import (
    JobRerunError,
    failed_hosts_for_rerun,
    rerun_job,
    rerun_preflight_issues,
)
from app.services.runner_environments import environment_revision


def _source_job(app, monkeypatch, tmp_path, *, requested_by="api-user", status="successful"):
    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )
    with app.app_context():
        project = Project(name="Rerun source project", execution_type="ansible", owner="admin")
        repository = Repository(
            name="Rerun repository",
            repository_type="local",
            url="/srv/ansible",
            default_branch="main",
        )
        db.session.add_all([project, repository])
        db.session.flush()

        job = Job(
            project_id=project.id,
            project_name=project.name,
            requested_by=requested_by,
            execution_type="ansible",
            status=status,
            dispatch_target="sliced",
            message="Original Job",
        )
        repository_snapshot = JobRepositorySnapshot(
            repository_id=repository.id,
            repository_name=repository.name,
            repository_url=repository.url,
            repository_commit="a" * 40,
        )
        inventory_snapshot = JobInventorySnapshot(
            inventory_id=None,
            inventory_name="Rerun inventory",
            inventory_type="static",
            version=1,
        )
        job.repository_snapshots.append(repository_snapshot)
        job.inventory_snapshots.append(inventory_snapshot)
        step = JobStep(
            repository_snapshot=repository_snapshot,
            inventory_snapshot=inventory_snapshot,
            position=1,
            name="Run playbook",
            environment_name="Default",
            environment_path="/opt/journeyman/venv",
            ansible_config_path="/etc/ansible/ansible.cfg",
            playbook="site.yml",
            status=status if status in {"successful", "failed", "cancelled"} else "pending",
            stdout="old output",
            exit_code=0 if status == "successful" else None,
        )
        execution_slice = JobStepExecutionSlice(
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status=step.status,
            stdout="old slice output",
            exit_code=step.exit_code,
        )
        execution_slice.set_hosts(["host1"])
        step.execution_slices.append(execution_slice)
        job.steps.append(step)
        db.session.add(job)
        db.session.flush()
        inventory_data = {
            "_meta": {"hostvars": {"host1": {"answer": 42}}},
            "all": {"hosts": ["host1"]},
        }
        write_job_inventory_snapshot(inventory_snapshot, inventory_data)
        db.session.commit()
        return job.id, inventory_data


def test_rerun_job_clones_immutable_snapshots_and_resets_execution_state(app, monkeypatch, tmp_path):
    source_id, inventory_data = _source_job(app, monkeypatch, tmp_path)

    with app.app_context():
        source = db.session.get(Job, source_id)
        result = rerun_job(source, requested_by="api-user")
        rerun = result.job

        assert rerun.id != source.id
        assert rerun.project_id == source.project_id
        assert rerun.project_name == source.project_name
        assert rerun.requested_by == "api-user"
        assert rerun.status == "queued"
        assert rerun.message == "Rerun of Job #{} through Journeyman API.".format(source.id)
        assert rerun.repository_snapshots[0].repository_commit == "a" * 40
        assert read_job_inventory_snapshot_data(rerun.inventory_snapshots[0]) == inventory_data
        assert rerun.inventory_snapshots[0].content_path != source.inventory_snapshots[0].content_path
        assert rerun.steps[0].playbook == "site.yml"
        assert rerun.steps[0].status == "pending"
        assert rerun.steps[0].stdout == ""
        assert rerun.steps[0].exit_code is None
        assert rerun.steps[0].execution_slices[0].get_hosts() == ["host1"]
        assert rerun.steps[0].execution_slices[0].status == "pending"
        assert rerun.steps[0].execution_slices[0].stdout == ""
        assert rerun.steps[0].execution_slices[0].exit_code is None


def test_failed_hosts_for_rerun_uses_final_recorded_status(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(
        app, monkeypatch, tmp_path, status="failed"
    )

    with app.app_context():
        source = db.session.get(Job, source_id)
        step = source.steps[0]
        step.execution_slices[0].set_hosts(["host1", "host2", "host3"])
        step.host_results.extend([
            JobStepHostResult(host="host1", status="failed"),
            JobStepHostResult(host="host2", status="unreachable"),
            JobStepHostResult(host="host3", status="successful"),
        ])

        later = JobStep(
            job=source,
            repository_snapshot=source.repository_snapshots[0],
            inventory_snapshot=source.inventory_snapshots[0],
            position=2,
            name="Later successful step",
            environment_name="Default",
            environment_path="/opt/journeyman/venv",
            ansible_config_path="/etc/ansible/ansible.cfg",
            playbook="later.yml",
            status="successful",
            exit_code=0,
        )
        later_slice = JobStepExecutionSlice(
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status="successful",
            exit_code=0,
        )
        later_slice.set_hosts(["host1"])
        later.execution_slices.append(later_slice)
        later.host_results.append(
            JobStepHostResult(host="host1", status="successful")
        )
        db.session.add(later)
        db.session.commit()

        assert failed_hosts_for_rerun(source) == ("host2",)


def test_failed_only_rerun_filters_saved_execution_slices(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(
        app, monkeypatch, tmp_path, status="failed"
    )

    with app.app_context():
        source = db.session.get(Job, source_id)
        step = source.steps[0]
        step.execution_slices[0].set_hosts(["host1", "host2", "host3"])
        step.host_results.extend([
            JobStepHostResult(host="host1", status="failed"),
            JobStepHostResult(host="host2", status="successful"),
            JobStepHostResult(host="host3", status="unreachable"),
        ])
        db.session.commit()

        result = rerun_job(
            source, requested_by="api-user", scope="failed"
        )
        rerun = result.job

        assert rerun.message == (
            "Rerun of Job #{} (failed hosts only) through Journeyman API."
            .format(source.id)
        )
        assert rerun.steps[0].limit == "host1,host3"
        assert rerun.steps[0].execution_slices[0].get_hosts() == ["host1", "host3"]
        assert rerun.steps[0].execution_slices[0].status == "pending"


def test_failed_only_rerun_rejects_job_without_rerunnable_failed_hosts(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(
        app, monkeypatch, tmp_path, status="failed"
    )

    with app.app_context():
        source = db.session.get(Job, source_id)
        source.steps[0].host_results.append(
            JobStepHostResult(host="host1", status="successful")
        )
        db.session.commit()

        with pytest.raises(JobRerunError, match="no saved failed or unreachable hosts"):
            rerun_job(source, requested_by="api-user", scope="failed")


def test_rerun_job_rejects_non_terminal_source(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(
        app, monkeypatch, tmp_path, status="queued"
    )
    with app.app_context():
        source = db.session.get(Job, source_id)
        try:
            rerun_job(source, requested_by="api-user")
        except JobRerunError as exc:
            assert "cannot be rerun until it has finished" in str(exc)
        else:
            raise AssertionError("queued Job was accepted as a rerun source")



def _configure_source_remote_environment(source, *, environment_name, runner_name, reported_revision=None):
    environment = Environment(
        name=environment_name,
        path="/opt/journeyman/{}".format(environment_name.lower().replace(" ", "-")),
        enabled=True,
        validation_status="passed",
        ansible_spec="ansible-core>=2.21,<2.22",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
    )
    runner = Runner(
        name=runner_name,
        hostname=runner_name,
        runner_uuid="{}-uuid".format(runner_name),
        api_secret_digest="a" * 64,
        enabled=True,
    )
    runner.set_capabilities({"ansible", "shell"})
    db.session.add_all([environment, runner])
    db.session.flush()

    revision = environment_revision(environment)
    step = source.steps[0]
    step.environment_id = environment.id
    step.environment_name = environment.name
    step.environment_revision = revision
    step.environment_path = environment.path

    execution_slice = step.execution_slices[0]
    execution_slice.dispatch_target = "remote"
    execution_slice.required_runner_id = runner.id
    execution_slice.runner_name = runner.name
    execution_slice.runner_hostname = runner.hostname
    execution_slice.set_required_capabilities({"ansible"})

    db.session.add(
        RunnerEnvironment(
            runner_id=runner.id,
            environment_id=environment.id,
            status="ready",
            environment_revision=reported_revision or revision,
            local_path="/opt/journeyman/environments/{}".format(environment.id),
        )
    )
    db.session.commit()
    return environment, runner, revision


def test_rerun_rejects_remote_environment_revision_drift(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(app, monkeypatch, tmp_path)

    with app.app_context():
        source = db.session.get(Job, source_id)
        _environment, _runner, required_revision = _configure_source_remote_environment(
            source,
            environment_name="Modern ansible",
            runner_name="rhel04",
            reported_revision="b" * 64,
        )

        issues = rerun_preflight_issues(source)
        assert len(issues) == 1
        assert 'requires Environment "Modern ansible" revision' in issues[0]
        assert "runner currently has revision" in issues[0]
        assert required_revision[:12] in issues[0]
        assert ("b" * 12) in issues[0]

        with pytest.raises(
            JobRerunError,
            match="cannot be rerun from its saved execution snapshot",
        ) as exc_info:
            rerun_job(source, requested_by="admin")
        assert "Launch the Project again" in str(exc_info.value)


def test_rerun_accepts_remote_environment_exact_revision(app, monkeypatch, tmp_path):
    source_id, _inventory_data = _source_job(app, monkeypatch, tmp_path)

    with app.app_context():
        source = db.session.get(Job, source_id)
        _configure_source_remote_environment(
            source,
            environment_name="Exact environment",
            runner_name="exact-runner",
        )

        assert rerun_preflight_issues(source) == []
        result = rerun_job(source, requested_by="admin")
        assert result.job.status == "queued"
        assert result.job.steps[0].environment_revision == source.steps[0].environment_revision
