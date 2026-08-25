from datetime import datetime, timezone

import pytest

from app import db
from app.models import (
    JobRepositorySnapshot,
    JobStep,
    JobStepExecutionSlice,
    Repository,
    Runner,
)
from app.services.execution_slices import (
    materialize_step_execution_slices,
    plan_step_execution_slices,
)
from app.services.inventory_runner_routing import InventoryRunnerRoutingError


def _inventory(hostvars):
    return {
        "all": {"hosts": list(hostvars), "children": []},
        "_meta": {"hostvars": hostvars},
    }


def _runner(name, hostname):
    runner = Runner(
        name=name,
        hostname=hostname,
        runner_uuid="{}-uuid".format(name),
        enabled=True,
        is_local=False,
        api_secret_digest="digest",
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    runner.set_capabilities(["ansible", "shell"])
    db.session.add(runner)
    db.session.flush()
    return runner


def test_slice_planner_uses_project_default_runner(app):
    with app.app_context():
        default = _runner("runner-a", "runner-a.example.com")
        plans = plan_step_execution_slices(
            inventory_data=_inventory({"host01": {}, "host02": {}}),
            target_hosts=("host01", "host02"),
            default_runner=default,
        )

        assert len(plans) == 1
        assert plans[0].dispatch_target == "remote"
        assert plans[0].required_runner_id == default.id
        assert plans[0].hosts == ("host01", "host02")


def test_slice_planner_fans_hosts_out_by_runner_override(app):
    with app.app_context():
        default = _runner("runner-a", "runner-a.example.com")
        override = _runner("runner-b", "runner-b.example.com")
        plans = plan_step_execution_slices(
            inventory_data=_inventory({
                "host01": {},
                "host02": {"journeyman_runner": "runner-b.example.com"},
                "host03": {"journeyman_runner": "runner-b.example.com"},
            }),
            target_hosts=("host01", "host02", "host03"),
            default_runner=default,
        )

        assert len(plans) == 2
        by_runner = {plan.runner_name: plan for plan in plans}
        assert by_runner["runner-a"].hosts == ("host01",)
        assert by_runner["runner-b"].hosts == ("host02", "host03")
        assert by_runner["runner-b"].required_runner_id == override.id


def test_slice_planner_allows_local_and_remote_hosts_in_one_step(app):
    with app.app_context():
        default = _runner("runner-a", "runner-a.example.com")
        plans = plan_step_execution_slices(
            inventory_data=_inventory({
                "localhost": {"ansible_connection": "local"},
                "host01": {},
            }),
            target_hosts=("localhost", "host01"),
            default_runner=default,
        )

        assert len(plans) == 2
        assert plans[0].dispatch_target == "local"
        assert plans[0].hosts == ("localhost",)
        assert plans[1].dispatch_target == "remote"
        assert plans[1].hosts == ("host01",)


def test_slice_planner_rejects_unknown_explicit_runner(app):
    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="no enabled registered remote runner",
        ):
            plan_step_execution_slices(
                inventory_data=_inventory({
                    "host01": {"journeyman_runner": "missing.example.com"},
                }),
                target_hosts=("host01",),
                default_runner=None,
            )


def test_slice_planner_only_groups_effective_target_hosts(app):
    with app.app_context():
        default = _runner("runner-a", "runner-a.example.com")
        plans = plan_step_execution_slices(
            inventory_data=_inventory({
                "host01": {},
                "host02": {"journeyman_runner": "missing.example.com"},
            }),
            target_hosts=("host01",),
            default_runner=default,
        )

        assert len(plans) == 1
        assert plans[0].hosts == ("host01",)


def test_execution_slice_serializes_hosts_and_capabilities(app):
    with app.app_context():
        item = JobStepExecutionSlice()
        item.set_hosts(["host02", "host01", "host01"])
        item.set_required_capabilities(["Shell", "ansible", "shell"])

        assert item.get_hosts() == ["host01", "host02"]
        assert item.host_count == 2
        assert item.get_required_capabilities() == {"ansible", "shell"}


def test_materialize_step_execution_slices_persists_plan_metadata(app):
    with app.app_context():
        default = _runner("runner-a", "runner-a.example.com")
        override = _runner("runner-b", "runner-b.example.com")
        plans = plan_step_execution_slices(
            inventory_data=_inventory({
                "host01": {},
                "host02": {"journeyman_runner": "runner-b.example.com"},
            }),
            target_hosts=("host01", "host02"),
            default_runner=default,
        )
        step = JobStep()

        rows = materialize_step_execution_slices(
            job_step=step,
            plans=plans,
            required_capabilities=["ansible"],
        )

        assert len(rows) == 2
        assert rows[0].position == 1
        assert rows[0].required_runner_id == default.id
        assert rows[0].get_hosts() == ["host01"]
        assert rows[1].position == 2
        assert rows[1].required_runner_id == override.id
        assert rows[1].get_hosts() == ["host02"]
        assert rows[1].get_required_capabilities() == {"ansible"}
        assert rows[0].status == "pending"


def _job_with_step(project_name="Slice dispatch test"):
    from app.models import Job, Project

    project = Project(name=project_name, owner="admin")
    repository = Repository(
        name="{} repository".format(project_name),
        url="https://example.invalid/{}.git".format(
            project_name.lower().replace(" ", "-"),
        ),
    )
    job = Job(
        project=project,
        project_name=project.name,
        status="queued",
        requested_by="admin",
        execution_type="ansible",
        dispatch_target="sliced",
    )
    db.session.add_all([project, repository, job])
    db.session.flush()

    repository_snapshot = JobRepositorySnapshot(
        job=job,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        repository_commit="0" * 40,
    )
    db.session.add(repository_snapshot)
    db.session.flush()

    step = JobStep(
        job=job,
        job_repository_snapshot_id=repository_snapshot.id,
        position=1,
        name="Step 1",
        playbook="site.yml",
        status="pending",
    )
    db.session.add(step)
    db.session.flush()
    return job, step


def test_remote_slice_claims_are_independent_per_runner(app):
    from app.services.runner_slice_dispatch import claim_next_remote_slice

    with app.app_context():
        runner_a = _runner("runner-a", "runner-a.example.com")
        runner_b = _runner("runner-b", "runner-b.example.com")
        job, step = _job_with_step()

        slice_a = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner_a.id,
            runner_name=runner_a.name,
            runner_hostname=runner_a.hostname,
            status="pending",
        )
        slice_a.set_hosts(["host-a"])
        slice_a.set_required_capabilities(["ansible"])
        slice_b = JobStepExecutionSlice(
            step=step,
            position=2,
            dispatch_target="remote",
            required_runner_id=runner_b.id,
            runner_name=runner_b.name,
            runner_hostname=runner_b.hostname,
            status="pending",
        )
        slice_b.set_hosts(["host-b"])
        slice_b.set_required_capabilities(["ansible"])
        db.session.add_all([slice_a, slice_b])
        db.session.commit()

        claimed_a, token_a = claim_next_remote_slice(runner_a)
        claimed_b, token_b = claim_next_remote_slice(runner_b)

        assert claimed_a.id == slice_a.id
        assert claimed_b.id == slice_b.id
        assert token_a and token_b and token_a != token_b
        assert claimed_a.assigned_runner_id == runner_a.id
        assert claimed_b.assigned_runner_id == runner_b.id
        assert job.status == "running"
        assert step.status == "running"


def test_remote_slice_manifest_limits_execution_to_slice_hosts(app):
    from app.services.runner_slice_dispatch import slice_assignment_manifest

    with app.app_context():
        runner = _runner("runner-a", "runner-a.example.com")
        _job, step = _job_with_step("Slice manifest test")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            assigned_runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            status="assigned",
        )
        execution_slice.set_hosts(["host02", "host01"])
        db.session.add(execution_slice)
        db.session.flush()

        manifest = slice_assignment_manifest(
            execution_slice,
            "token",
            repository_artifacts=[],
            execution_data_url="https://jm/api/data",
        )

        assert manifest["assignment_type"] == "slice"
        assert manifest["slice_id"] == execution_slice.id
        assert len(manifest["steps"]) == 1
        assert manifest["steps"][0]["limit"] == "host01,host02"
        assert manifest["steps"][0]["depends_on"] == []
        assert manifest["steps"][0]["refresh_inventory_after"] is False


def test_remote_slice_completion_aggregates_step_and_job(app):
    from app.services.runner_slice_lifecycle import (
        complete_remote_slice,
        start_remote_slice,
    )

    with app.app_context():
        runner_a = _runner("runner-a", "runner-a.example.com")
        runner_b = _runner("runner-b", "runner-b.example.com")
        job, step = _job_with_step("Slice completion test")
        job.status = "running"
        step.status = "running"

        slices = []
        for position, runner, host, token in (
            (1, runner_a, "host-a", "token-a"),
            (2, runner_b, "host-b", "token-b"),
        ):
            item = JobStepExecutionSlice(
                step=step,
                position=position,
                dispatch_target="remote",
                required_runner_id=runner.id,
                assigned_runner_id=runner.id,
                runner_name=runner.name,
                runner_hostname=runner.hostname,
                status="assigned",
                dispatch_token=token,
            )
            item.set_hosts([host])
            slices.append(item)
            db.session.add(item)
        db.session.commit()

        assert start_remote_slice(slices[0], runner_a, "token-a")[0]
        assert complete_remote_slice(
            slices[0],
            runner_a,
            "token-a",
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook",
                    "stdout": "runner-a output",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]
        assert step.status == "running"
        assert job.status == "running"

        assert start_remote_slice(slices[1], runner_b, "token-b")[0]
        assert complete_remote_slice(
            slices[1],
            runner_b,
            "token-b",
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook",
                    "stdout": "runner-b output",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]

        assert step.status == "successful"
        assert job.status == "successful"
        assert job.exit_code == 0
        assert "runner-a output" in step.stdout
        assert "runner-b output" in step.stdout


def test_local_slice_completion_waits_for_remote_and_records_runner_provenance(app):
    from app.services.runner_slice_lifecycle import (
        complete_local_slice,
        complete_remote_slice,
        start_local_slice,
        start_remote_slice,
    )

    with app.app_context():
        local_runner = Runner(
            name="journeyman local runner",
            hostname="journeyman.example.com",
            runner_uuid="local:journeyman",
            enabled=True,
            is_local=True,
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        local_runner.set_capabilities(["ansible", "shell"])
        remote_runner = _runner("runner01", "runner01.example.com")
        db.session.add(local_runner)
        db.session.flush()

        job, step = _job_with_step("Mixed local remote slice test")
        job.status = "queued"

        local_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status="pending",
        )
        local_slice.set_hosts(["app02", "app03"])
        local_slice.set_required_capabilities(["ansible"])
        remote_slice = JobStepExecutionSlice(
            step=step,
            position=2,
            dispatch_target="remote",
            required_runner_id=remote_runner.id,
            assigned_runner_id=remote_runner.id,
            runner_name=remote_runner.name,
            runner_hostname=remote_runner.hostname,
            status="assigned",
            dispatch_token="remote-token",
        )
        remote_slice.set_hosts(["app01"])
        remote_slice.set_required_capabilities(["ansible"])
        db.session.add_all([local_slice, remote_slice])
        db.session.commit()

        assert start_local_slice(local_slice, local_runner)[0]
        assert complete_local_slice(
            local_slice,
            local_runner,
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook --limit app02,app03",
                    "stdout": "local output",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]
        assert step.status == "running"
        assert job.status == "running"

        assert start_remote_slice(remote_slice, remote_runner, "remote-token")[0]
        assert complete_remote_slice(
            remote_slice,
            remote_runner,
            "remote-token",
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook --limit app01",
                    "stdout": "remote output",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]

        assert job.status == "successful"
        results = {item.host: item for item in step.host_results}
        assert set(results) == {"app01", "app02", "app03"}
        assert results["app01"].runner_hostname == "runner01.example.com"
        assert results["app01"].runner_local is False
        assert results["app02"].runner_hostname == "journeyman.example.com"
        assert results["app02"].runner_local is True
        assert results["app03"].runner_hostname == "journeyman.example.com"
        assert results["app03"].runner_local is True

def test_lost_remote_runner_fails_only_its_slice_and_does_not_retry(app):
    from datetime import timedelta

    from app.services.runner_recovery import recover_lost_runner_jobs
    from app.services.runner_slice_lifecycle import (
        complete_local_slice,
        start_local_slice,
    )

    with app.app_context():
        now = datetime.now(timezone.utc)
        lost_runner = _runner("lost-runner", "lost-runner.example.com")
        lost_runner.last_heartbeat_at = now - timedelta(minutes=10)

        local_runner = Runner(
            name="local runner",
            hostname="journeyman.example.com",
            runner_uuid="local:journeyman",
            enabled=True,
            is_local=True,
            last_heartbeat_at=now,
        )
        local_runner.set_capabilities(["ansible", "shell"])
        db.session.add(local_runner)
        db.session.flush()

        job, step = _job_with_step("Lost mixed slice test")
        job.status = "running"
        step.status = "running"

        remote_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=lost_runner.id,
            assigned_runner_id=lost_runner.id,
            runner_name=lost_runner.name,
            runner_hostname=lost_runner.hostname,
            status="running",
            dispatch_token="lost-token",
        )
        remote_slice.set_hosts(["app01"])
        remote_slice.set_required_capabilities(["ansible"])

        local_slice = JobStepExecutionSlice(
            step=step,
            position=2,
            dispatch_target="local",
            runner_name=local_runner.name,
            runner_hostname=local_runner.hostname,
            status="pending",
        )
        local_slice.set_hosts(["app02", "app03"])
        local_slice.set_required_capabilities(["ansible"])
        db.session.add_all([remote_slice, local_slice])
        db.session.commit()

        result = recover_lost_runner_jobs(now=now)

        assert result["slices_failed"] == [remote_slice.id]
        assert result["slices_cancelled"] == []
        assert remote_slice.status == "failed"
        assert remote_slice.dispatch_token == ""
        assert local_slice.status == "pending"
        assert step.status == "running"
        assert job.status == "running"
        assert "not automatically retried" in remote_slice.message

        results = {item.host: item for item in step.host_results}
        assert results["app01"].status == "failed"
        assert results["app01"].runner_hostname == lost_runner.hostname

        assert start_local_slice(local_slice, local_runner)[0]
        assert complete_local_slice(
            local_slice,
            local_runner,
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook",
                    "stdout": "local hosts completed",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]

        assert local_slice.status == "successful"
        assert step.status == "failed"
        assert job.status == "failed"
        assert job.exit_code == 1

        client = app.test_client()
        response = client.get(
            "/jobs/{}".format(job.id),
            headers={"X-Test-Username": "admin"},
        )
        assert response.status_code == 200
        assert b"Execution Slices" in response.data
        assert b"not automatically retried" in response.data
        assert lost_runner.hostname.encode("utf-8") in response.data


def test_lost_remote_slice_is_cancelled_when_job_is_cancelling(app):
    from datetime import timedelta

    from app.services.runner_recovery import recover_lost_runner_jobs

    with app.app_context():
        now = datetime.now(timezone.utc)
        lost_runner = _runner("lost-cancel-runner", "lost-cancel.example.com")
        lost_runner.last_heartbeat_at = now - timedelta(minutes=10)
        job, step = _job_with_step("Lost cancelling slice test")
        job.status = "cancelling"
        step.status = "running"

        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=lost_runner.id,
            assigned_runner_id=lost_runner.id,
            runner_name=lost_runner.name,
            runner_hostname=lost_runner.hostname,
            status="running",
            dispatch_token="cancel-token",
        )
        execution_slice.set_hosts(["app01"])
        db.session.add(execution_slice)
        db.session.commit()

        result = recover_lost_runner_jobs(now=now)

        assert result["slices_cancelled"] == [execution_slice.id]
        assert execution_slice.status == "cancelled"
        assert step.status == "cancelled"
        assert job.status == "cancelled"


def test_successful_sliced_step_refreshes_only_after_all_slices_complete(
    app,
    monkeypatch,
):
    from app.services import job_inventory_refresh
    from app.services.runner_slice_lifecycle import (
        complete_remote_slice,
        start_remote_slice,
    )

    with app.app_context():
        runner_a = _runner("refresh-runner-a", "refresh-a.example.com")
        runner_b = _runner("refresh-runner-b", "refresh-b.example.com")
        job, step = _job_with_step("Slice refresh lifecycle test")
        job.status = "running"
        step.status = "running"
        step.refresh_inventory_after = True

        slices = []
        for position, runner, host, token in (
            (1, runner_a, "host-a", "refresh-token-a"),
            (2, runner_b, "host-b", "refresh-token-b"),
        ):
            item = JobStepExecutionSlice(
                step=step,
                position=position,
                dispatch_target="remote",
                required_runner_id=runner.id,
                assigned_runner_id=runner.id,
                runner_name=runner.name,
                runner_hostname=runner.hostname,
                status="assigned",
                dispatch_token=token,
            )
            item.set_hosts([host])
            item.set_required_capabilities(["ansible"])
            db.session.add(item)
            slices.append(item)
        db.session.commit()

        refresh_calls = []

        def fake_refresh(_job, trigger_step):
            refresh_calls.append(trigger_step.position)
            return []

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_job_inventories_after_step",
            fake_refresh,
        )

        assert start_remote_slice(
            slices[0], runner_a, "refresh-token-a"
        )[0]
        assert complete_remote_slice(
            slices[0],
            runner_a,
            "refresh-token-a",
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook",
                    "stdout": "first slice",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]
        assert refresh_calls == []

        assert start_remote_slice(
            slices[1], runner_b, "refresh-token-b"
        )[0]
        assert complete_remote_slice(
            slices[1],
            runner_b,
            "refresh-token-b",
            {
                "status": "successful",
                "exit_code": 0,
                "steps": [{
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook",
                    "stdout": "second slice",
                    "stderr": "",
                    "host_results": [],
                }],
            },
        )[0]

        assert refresh_calls == [1]


def test_remote_slice_live_output_update_persists_snapshot(app):
    from app.services.runner_slice_lifecycle import update_remote_slice_output

    with app.app_context():
        runner = _runner("runner-output", "runner-output.example.com")
        _job, step = _job_with_step("Slice live output test")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            assigned_runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            status="running",
            dispatch_token="slice-token",
        )
        execution_slice.set_hosts(["host01"])
        db.session.add(execution_slice)
        db.session.commit()

        accepted, result = update_remote_slice_output(
            execution_slice,
            runner,
            "slice-token",
            {
                "command": "ansible-playbook site.yml",
                "stdout": "first line\nsecond line\n",
                "stderr": "warning\n",
            },
        )

        assert accepted is True
        assert result == "updated"
        assert execution_slice.command == "ansible-playbook site.yml"
        assert execution_slice.stdout == "first line\nsecond line\n"
        assert execution_slice.stderr == "warning\n"


def test_remote_slice_live_output_rejects_wrong_dispatch_token(app):
    from app.services.runner_slice_lifecycle import update_remote_slice_output

    with app.app_context():
        runner = _runner("runner-output-token", "runner-output-token.example.com")
        _job, step = _job_with_step("Slice live output token test")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            assigned_runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            status="running",
            dispatch_token="correct-token",
        )
        db.session.add(execution_slice)
        db.session.commit()

        accepted, result = update_remote_slice_output(
            execution_slice,
            runner,
            "wrong-token",
            {"stdout": "must not be stored"},
        )

        assert accepted is False
        assert result == "assignment_mismatch"
        assert execution_slice.stdout == ""


def test_job_slice_output_route_returns_only_requested_job_slice(app, client):
    with app.app_context():
        job, step = _job_with_step("Slice output route test")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="journeyman.example.com",
            status="running",
            command="ansible-playbook site.yml",
            stdout="live stdout\n",
            stderr="live stderr\n",
        )
        db.session.add(execution_slice)
        db.session.commit()
        job_id = job.id
        slice_id = execution_slice.id

    response = client.get(
        "/jobs/{}/slices/{}/output".format(job_id, slice_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "running"
    assert payload["terminal"] is False
    assert payload["runner"] == "journeyman.example.com"
    assert payload["stdout"] == "live stdout\n"
    assert payload["stderr"] == "live stderr\n"


def test_job_slice_output_route_blocks_unrelated_user(app, client):
    with app.app_context():
        job, step = _job_with_step("Slice output authorization test")
        job.requested_by = "owner.user"
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="local",
            status="successful",
            stdout="sensitive job output\n",
        )
        db.session.add(execution_slice)
        db.session.commit()
        job_id = job.id
        slice_id = execution_slice.id

    response = client.get(
        "/jobs/{}/slices/{}/output".format(job_id, slice_id),
        headers={"X-Test-Username": "unrelated.user"},
    )

    assert response.status_code == 403



def test_job_step_output_route_returns_direct_step_output(app, client):
    with app.app_context():
        job, step = _job_with_step("Direct step output route test")
        step.status = "running"
        step.command = "ansible-playbook direct.yml"
        step.stdout = "direct stdout\n"
        step.stderr = "direct stderr\n"
        db.session.commit()
        job_id = job.id
        step_id = step.id

    response = client.get(
        "/jobs/{}/steps/{}/output".format(job_id, step_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "running"
    assert payload["terminal"] is False
    assert payload["runner"] == "Built-in local runner"
    assert payload["command"] == "ansible-playbook direct.yml"
    assert payload["stdout"] == "direct stdout\n"
    assert payload["stderr"] == "direct stderr\n"


def test_job_step_output_route_blocks_unrelated_user(app, client):
    with app.app_context():
        job, step = _job_with_step("Direct step output authorization test")
        job.requested_by = "owner.user"
        step.stdout = "sensitive direct output\n"
        db.session.commit()
        job_id = job.id
        step_id = step.id

    response = client.get(
        "/jobs/{}/steps/{}/output".format(job_id, step_id),
        headers={"X-Test-Username": "unrelated.user"},
    )

    assert response.status_code == 403


def test_job_detail_shows_output_for_direct_step_without_slices(app, client):
    with app.app_context():
        job, step = _job_with_step("Direct output button test")
        step.command = "ansible-playbook direct.yml"
        db.session.commit()
        job_id = job.id
        step_id = step.id

    response = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Show job output" in page
    assert "/jobs/{}/steps/{}/output".format(job_id, step_id) in page


def test_job_detail_shows_execution_slices_for_single_slice(app, client):
    with app.app_context():
        job, step = _job_with_step("Single slice output button test")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="journeyman.example.com",
            status="running",
        )
        db.session.add(execution_slice)
        db.session.commit()
        job_id = job.id
        slice_id = execution_slice.id

    response = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Execution Slices" in page
    assert "Show job output" in page
    assert "/jobs/{}/slices/{}/output".format(job_id, slice_id) in page


def test_slice_planner_selects_least_busy_project_default_crew(app):
    from app.models import RunnerCrew

    with app.app_context():
        busy = _runner("runner-busy", "runner-busy.example.com")
        idle = _runner("runner-idle", "runner-idle.example.com")
        busy.running_steps = 2
        idle.running_steps = 0
        busy.max_concurrent_steps = 4
        idle.max_concurrent_steps = 4
        crew = RunnerCrew(name="Melbourne", runners=[busy, idle])
        db.session.add(crew)
        db.session.flush()

        plans = plan_step_execution_slices(
            inventory_data=_inventory({"host01": {}, "host02": {}}),
            target_hosts=("host01", "host02"),
            default_runner=None,
            default_runner_crew=crew,
            required_capabilities={"ansible"},
        )

        assert len(plans) == 1
        assert plans[0].required_runner_id == idle.id
        assert plans[0].hosts == ("host01", "host02")


def test_reconcile_treats_eligible_zero_host_slice_as_successful_noop(app):
    from app.services.runner_slice_dispatch import reconcile_non_runnable_steps

    with app.app_context():
        job, step = _job_with_step("Failed-only zero-host rerun")
        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status="successful",
            exit_code=0,
            message="No failed hosts from the source Job target this execution slice.",
        )
        execution_slice.set_hosts([])
        db.session.add(execution_slice)
        db.session.commit()

        assert reconcile_non_runnable_steps(job) is True
        assert step.status == "successful"
        assert step.exit_code == 0
