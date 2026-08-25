from datetime import datetime, timezone

import pytest

from app import db
from app.models import (
    Environment,
    Job,
    JobRepositorySnapshot,
    JobStep,
    JobStepExecutionSlice,
    Project,
    Repository,
    Runner,
    RunnerCrew,
    RunnerEnvironment,
)
from app.services.execution_slices import plan_step_execution_slices
from app.services.inventory_runner_routing import InventoryRunnerRoutingError
from app.services.runner_crews import select_crew_runner
from app.services.runner_dispatch import job_assignment_manifest, runner_can_claim
from app.services.runner_environments import (
    SYSTEM_ENVIRONMENT_REVISION,
    environment_requirement,
    environment_revision,
    runner_environment_state,
)
from app.services.runner_slice_dispatch import runner_can_claim_slice
from app.services.runners import CURRENT_REMOTE_RUNNER_VERSION


def _runner(name):
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


def _environment(name="Ansible Modern"):
    environment = Environment(
        name=name,
        path="/opt/journeyman/environments/{}".format(name.lower().replace(" ", "-")),
        enabled=True,
        is_managed=True,
        ansible_spec="ansible-core==2.21.3",
        pip_requirements="ovirt-engine-sdk-python\nrequests",
        collection_requirements="ovirt.ovirt:3.2.1\ncommunity.vmware:5.7.0",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
        validation_status="passed",
    )
    db.session.add(environment)
    db.session.flush()
    return environment


def _ready(runner, environment, *, path=None, revision=None):
    row = RunnerEnvironment(
        runner=runner,
        environment=environment,
        status="ready",
        environment_revision=revision or environment_revision(environment),
        local_path=path or "/opt/journeyman/environments/runner-copy",
        message="Ready",
        last_reported_at=datetime.now(timezone.utc),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _inventory(hostvars):
    return {
        "all": {"hosts": list(hostvars), "children": []},
        "_meta": {"hostvars": hostvars},
    }


def _job_step(runner, environment):
    project = Project(name="Environment claim test", owner="admin")
    repository = Repository(
        name="Environment claim repository",
        url="https://example.invalid/environment-claim.git",
    )
    job = Job(
        project=project,
        project_name=project.name,
        status="queued",
        requested_by="admin",
        execution_type="ansible",
        dispatch_target="remote",
        required_runner_id=runner.id,
        required_runner_capabilities_json='["ansible"]',
    )
    db.session.add_all([project, repository, job])
    db.session.flush()
    snapshot = JobRepositorySnapshot(
        job=job,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        repository_commit="0" * 40,
    )
    db.session.add(snapshot)
    db.session.flush()
    requirement = environment_requirement(environment)
    step = JobStep(
        job=job,
        job_repository_snapshot_id=snapshot.id,
        position=1,
        name="Validate VM",
        environment_name=environment.name,
        environment_id=environment.id,
        environment_revision=requirement.revision,
        environment_path=environment.path,
        playbook="validate.yml",
        status="pending",
    )
    db.session.add(step)
    db.session.flush()
    return job, step


def test_environment_revision_is_portable_and_excludes_local_venv_path():
    first = Environment(
        name="Portable",
        path="/opt/journeyman/environments/portable",
        is_managed=True,
        ansible_spec="ansible-core==2.21.3",
        pip_requirements="requests",
        collection_requirements="community.general:11.3.0",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
    )
    second = Environment(
        name="Portable",
        path="/some/other/node/local/path",
        is_managed=True,
        ansible_spec=first.ansible_spec,
        pip_requirements=first.pip_requirements,
        collection_requirements=first.collection_requirements,
        python_version=first.python_version,
        ansible_version=first.ansible_version,
    )

    assert environment_revision(first) == environment_revision(second)


def test_system_ansible_has_stable_runner_revision():
    environment = Environment(name="System Ansible", path="__SYSTEM_ANSIBLE__")
    assert environment_revision(environment) == SYSTEM_ENVIRONMENT_REVISION


def test_runner_environment_ready_requires_matching_revision(app):
    with app.app_context():
        runner = _runner("runner-a")
        environment = _environment()
        requirement = environment_requirement(environment)

        assert runner_environment_state(runner, requirement) == "not_installed"

        row = _ready(runner, environment, revision="0" * 64)
        assert runner_environment_state(runner, requirement) == "out_of_date"

        row.environment_revision = requirement.revision
        assert runner_environment_state(runner, requirement) == "ready"


def test_slice_preflight_rejects_remote_runner_missing_environment(app):
    with app.app_context():
        runner = _runner("runner-a")
        environment = _environment()

        with pytest.raises(
            InventoryRunnerRoutingError,
            match='does not have execution environment "Ansible Modern" ready',
        ):
            plan_step_execution_slices(
                inventory_data=_inventory({"vm01": {}}),
                target_hosts=("vm01",),
                default_runner=runner,
                required_capabilities={"ansible"},
                required_environment=environment_requirement(environment),
            )


def test_runner_crew_filters_members_by_environment_revision(app):
    with app.app_context():
        stale = _runner("stale")
        ready = _runner("ready")
        environment = _environment()
        _ready(stale, environment, revision="0" * 64)
        _ready(ready, environment)
        crew = RunnerCrew(name="Sydney", runners=[stale, ready])
        db.session.add(crew)
        db.session.flush()

        selected = select_crew_runner(
            crew,
            required_capabilities={"ansible"},
            required_environment=environment_requirement(environment),
        )
        assert selected is ready


def test_remote_claim_revalidates_environment_and_manifest_uses_runner_path(app):
    with app.app_context():
        runner = _runner("runner-a")
        environment = _environment()
        job, step = _job_step(runner, environment)
        db.session.commit()

        assert runner_can_claim(runner, job) is False

        local_path = "/opt/journeyman/runner-environments/ansible-modern"
        _ready(runner, environment, path=local_path)
        job.assigned_runner_id = runner.id
        db.session.commit()

        assert runner_can_claim(runner, job) is True
        manifest = job_assignment_manifest(job, "token")
        assert manifest["steps"][0]["environment_path"] == local_path
        assert manifest["steps"][0]["environment_path"] != environment.path
        assert manifest["steps"][0]["environment_revision"] == environment_revision(environment)


def test_remote_slice_claim_revalidates_environment(app):
    with app.app_context():
        runner = _runner("runner-a")
        environment = _environment()
        job, step = _job_step(runner, environment)
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            status="pending",
        )
        execution_slice.set_hosts(["vm01"])
        execution_slice.set_required_capabilities(["ansible"])
        db.session.add(execution_slice)
        db.session.flush()

        assert runner_can_claim_slice(runner, execution_slice) is False
        _ready(runner, environment)
        assert runner_can_claim_slice(runner, execution_slice) is True


def test_runner_heartbeat_persists_reported_environment_state(app, client):
    from app.services.runners import issue_registration_token

    with app.app_context():
        environment = _environment("VM Migration")
        runner = Runner(name="heartbeat-runner", site="test")
        runner.set_capabilities(["ansible"])
        token = issue_registration_token(runner)
        db.session.add(runner)
        db.session.commit()
        environment_id = environment.id
        expected_revision = environment_revision(environment)

    registration = client.post(
        "/api/runners/register",
        json={"token": token, "hostname": "runner.example.com", "version": "0.9"},
    )
    assert registration.status_code == 200
    credentials = registration.get_json()
    headers = {
        "X-Journeyman-Runner-ID": credentials["runner_uuid"],
        "Authorization": "Bearer {}".format(credentials["runner_secret"]),
    }

    heartbeat = client.post(
        "/api/runners/heartbeat",
        headers=headers,
        json={
            "hostname": "runner.example.com",
            "version": "0.9",
            "status_message": "Ready",
            "running_steps": 0,
            "capabilities": ["ansible", "shell"],
            "environments": [
                {
                    "environment_id": environment_id,
                    "name": "VM Migration",
                    "revision": expected_revision,
                    "path": "/opt/journeyman/environments/vm-migration",
                    "status": "ready",
                    "message": "Ready",
                }
            ],
        },
    )
    assert heartbeat.status_code == 200

    with app.app_context():
        row = RunnerEnvironment.query.filter_by(environment_id=environment_id).one()
        assert row.status == "ready"
        assert row.environment_revision == expected_revision
        assert row.local_path == "/opt/journeyman/environments/vm-migration"


def test_application_runtime_is_not_listed_as_runner_execution_environment(app):
    from app.services.environments import APPLICATION_ENVIRONMENT_NAME
    from app.services.runner_environments import runner_environment_rows

    with app.app_context():
        application = Environment(
            name=APPLICATION_ENVIRONMENT_NAME,
            path="/opt/journeyman/venv314",
            enabled=True,
            is_builtin=True,
            validation_status="passed",
        )
        environment = _environment("Modern ansible")
        runner = _runner("runner-environment-list")
        db.session.add(application)
        db.session.flush()

        names = [
            item["environment"].name
            for item in runner_environment_rows(runner)
        ]

        assert APPLICATION_ENVIRONMENT_NAME not in names
        assert environment.name in names


def test_managed_environment_sync_queue_claim_and_complete(app):
    from app.models import RunnerEnvironmentSync
    from app.services.runner_environment_sync import (
        claim_next_environment_sync,
        complete_environment_sync,
        environment_sync_manifest,
        queue_environment_sync,
    )

    with app.app_context():
        runner = _runner("sync-runner")
        environment = _environment("Modern ansible")
        environment.build_status = "passed"
        environment.python_interpreter = "/usr/bin/python3.14"
        db.session.flush()

        sync = queue_environment_sync(environment, runner)
        db.session.commit()
        sync_id = sync.id

        claimed = claim_next_environment_sync(runner)
        assert claimed.id == sync_id
        assert claimed.status == "building"

        manifest = environment_sync_manifest(claimed)
        assert manifest["environment"]["environment_id"] == environment.id
        assert manifest["environment"]["name"] == environment.name
        assert manifest["environment"]["revision"] == environment_revision(environment)
        assert manifest["environment"]["python_command"] == "python3.14"
        assert "ovirt-engine-sdk-python" in manifest["environment"]["pip_requirements"]

        complete_environment_sync(
            claimed,
            runner,
            {
                "status": "ready",
                "revision": manifest["environment"]["revision"],
                "path": "/opt/journeyman/environments/42-modern-ansible",
                "message": "Environment synchronized successfully.",
            },
        )

        completed = db.session.get(RunnerEnvironmentSync, sync_id)
        assert completed.status == "successful"
        state = RunnerEnvironment.query.filter_by(
            runner_id=runner.id,
            environment_id=environment.id,
        ).one()
        assert state.status == "ready"
        assert state.environment_revision == environment_revision(environment)
        assert state.local_path == "/opt/journeyman/environments/42-modern-ansible"


def test_environment_sync_completion_tolerates_concurrent_state_insert(app, monkeypatch):
    from app.services.runner_environment_sync import (
        claim_next_environment_sync,
        complete_environment_sync,
        environment_sync_manifest,
        queue_environment_sync,
    )

    with app.app_context():
        runner = _runner("sync-race-runner")
        environment = _environment("Cisco IOS")
        environment.build_status = "passed"
        db.session.flush()

        sync = queue_environment_sync(environment, runner)
        db.session.commit()
        claimed = claim_next_environment_sync(runner)
        manifest = environment_sync_manifest(claimed)

        # Model the heartbeat winning the INSERT after /complete has already
        # observed no state row.  The unique constraint must resolve the race
        # without poisoning the outer synchronization transaction.
        existing = RunnerEnvironment(
            runner=runner,
            environment=environment,
            status="building",
            environment_revision=manifest["environment"]["revision"],
            local_path="/opt/journeyman/environments/race-copy",
            message="Heartbeat reported Environment.",
            last_reported_at=datetime.now(timezone.utc),
        )
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

        original_query = RunnerEnvironment.query

        class RaceQuery:
            def __init__(self):
                self.filter_calls = 0

            def filter_by(self, **filters):
                self.filter_calls += 1
                if self.filter_calls == 1:
                    class MissingResult:
                        @staticmethod
                        def one_or_none():
                            return None

                    return MissingResult()
                return original_query.filter_by(**filters)

        monkeypatch.setattr(RunnerEnvironment, "query", RaceQuery(), raising=False)

        complete_environment_sync(
            claimed,
            runner,
            {
                "status": "ready",
                "revision": manifest["environment"]["revision"],
                "path": "/opt/journeyman/environments-rhel19/3-cisco-ios",
                "message": "Environment synchronized successfully.",
            },
        )

        completed = db.session.get(type(claimed), claimed.id)
        state = db.session.get(RunnerEnvironment, existing_id)
        assert completed.status == "successful"
        assert state.status == "ready"
        assert state.local_path == "/opt/journeyman/environments-rhel19/3-cisco-ios"
        assert original_query.filter_by(
            runner_id=runner.id,
            environment_id=environment.id,
        ).count() == 1


def test_sync_queue_does_not_overwrite_existing_ready_revision_until_completion(app):
    from app.services.runner_environment_sync import queue_environment_sync

    with app.app_context():
        runner = _runner("sync-preserve-runner")
        environment = _environment("Cisco IOS")
        environment.build_status = "passed"
        old_revision = "0" * 64
        state = _ready(runner, environment, revision=old_revision)
        db.session.flush()

        queue_environment_sync(environment, runner)
        db.session.flush()

        assert state.status == "ready"
        assert state.environment_revision == old_revision
        assert runner_environment_state(
            runner, environment_requirement(environment)
        ) == "out_of_date"


def test_environment_revision_tracks_ansible_config_content(tmp_path):
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nforks=5\n", encoding="utf-8")
    environment = Environment(
        name="Config revision",
        path="/opt/journeyman/environments/config-revision",
        is_managed=True,
        ansible_spec="ansible-core==2.21.3",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
        ansible_config_path=str(config),
    )

    first = environment_revision(environment)
    config.write_text("[defaults]\nforks=10\n", encoding="utf-8")
    second = environment_revision(environment)

    assert first != second


def test_environment_revision_ignores_ansible_core_patch_drift():
    environment = Environment(
        name="Patch compatible",
        path="/opt/journeyman/environments/patch-compatible",
        is_managed=True,
        ansible_spec="ansible-core>=2.21,<2.22",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.2]",
    )
    first = environment_revision(environment)
    environment.ansible_version = "ansible-playbook [core 2.21.3]"
    second = environment_revision(environment)
    assert first == second
    environment.ansible_version = "ansible-playbook [core 2.22.0]"
    assert environment_revision(environment) != first


def test_validated_external_environment_can_be_synchronized(app):
    from app.services.runner_environment_sync import (
        environment_sync_manifest,
        is_syncable_environment,
        queue_environment_sync,
    )

    with app.app_context():
        runner = _runner("external-sync-runner")
        environment = Environment(
            name="Modern ansible",
            path="/opt/journeyman/venv-ansible-modern",
            enabled=True,
            is_managed=False,
            ansible_spec="ansible-core",
            pip_requirements="requests",
            python_version="Python 3.14.5",
            ansible_version="ansible-playbook [core 2.21.3]",
            validation_status="passed",
            build_status="not_built",
        )
        db.session.add(environment)
        db.session.flush()

        assert is_syncable_environment(environment) is True
        sync = queue_environment_sync(environment, runner)
        db.session.commit()
        manifest = environment_sync_manifest(sync)
        assert manifest["environment"]["ansible_compatibility_requirement"] == "ansible-core>=2.21,<2.22"
        assert manifest["environment"]["pip_requirements"] == ["requests"]


def test_environment_revision_tracks_runner_system_requirements():
    environment = Environment(
        name="Runner packages",
        path="/opt/journeyman/environments/runner-packages",
        is_managed=True,
        ansible_spec="ansible-core>=2.21,<2.22",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
        system_requirements="adcli",
    )
    first = environment_revision(environment)
    environment.system_requirements = "adcli\nkrb5-workstation"
    assert environment_revision(environment) != first


def test_environment_revision_unchanged_for_empty_runner_system_requirements():
    environment = Environment(
        name="No runner packages",
        path="/opt/journeyman/environments/no-runner-packages",
        is_managed=True,
        ansible_spec="ansible-core>=2.21,<2.22",
        python_version="Python 3.14.5",
        ansible_version="ansible-playbook [core 2.21.3]",
        system_requirements="",
    )
    without_value = environment_revision(environment)
    environment.system_requirements = "\n   \n"
    assert environment_revision(environment) == without_value



def test_out_of_date_remote_slice_is_failed_instead_of_left_pending(app):
    from app.services.runner_slice_dispatch import claim_next_remote_slice

    with app.app_context():
        runner = _runner("stale-runner")
        environment = _environment("Queued stale environment")
        job, step = _job_step(runner, environment)
        job.dispatch_target = "sliced"
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            required_runner_capabilities_json='["ansible"]',
            status="pending",
        )
        execution_slice.set_hosts(["host1"])
        db.session.add(execution_slice)
        _ready(runner, environment, revision="e" * 64)
        db.session.commit()
        slice_id = execution_slice.id
        job_id = job.id

        claimed_slice, token = claim_next_remote_slice(runner)
        assert claimed_slice is None
        assert token is None

        refreshed_job = db.session.get(Job, job_id)
        refreshed_slice = db.session.get(JobStepExecutionSlice, slice_id)
        assert refreshed_slice.status == "failed"
        assert "saved Job snapshot can no longer be satisfied" in refreshed_slice.message
        assert refreshed_job.status == "failed"
        assert refreshed_job.finished_at is not None
