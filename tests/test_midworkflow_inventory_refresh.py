import json

from app import db
from app.models import (
    Inventory,
    Job,
    JobInventorySnapshot,
    JobStep,
)
from app.services import job_inventory_refresh
from app.services.job_inventory_refresh import (
    refresh_job_inventories_after_step,
)
from app.services.job_inventory_snapshot import (
    read_job_inventory_snapshot_data,
    write_job_inventory_snapshot,
)


def _inventory_data(*hosts):
    return {
        "_meta": {
            "hostvars": {
                host: {}
                for host in hosts
            }
        },
        "all": {
            "children": ["targets"],
        },
        "targets": {
            "hosts": list(hosts),
        },
    }


def _job_with_dependency_chain():
    inventory = Inventory(
        name="Dynamic inventory",
        inventory_type="static",
        enabled=True,
        config_json=json.dumps(
            {
                "inventory": _inventory_data(
                    "old-host"
                ),
            }
        ),
        status="ok",
    )
    db.session.add(inventory)
    db.session.flush()

    job = Job(
        project_id=1,
        project_name="Refresh test",
        requested_by="admin",
        execution_type="ansible",
        status="running",
    )
    db.session.add(job)
    db.session.flush()

    original = JobInventorySnapshot(
        job=job,
        inventory_id=inventory.id,
        inventory_name=inventory.name,
        inventory_type=inventory.inventory_type,
        version=1,
    )
    db.session.add(original)
    db.session.flush()

    write_job_inventory_snapshot(
        original,
        _inventory_data("old-host"),
    )

    first = JobStep(
        job=job,
        position=1,
        name="Provision",
        playbook="provision.yml",
        job_repository_snapshot_id=1,
        inventory_snapshot=original,
        status="successful",
        refresh_inventory_after=True,
        depends_on_json="[]",
    )
    second = JobStep(
        job=job,
        position=2,
        name="Configure",
        playbook="configure.yml",
        job_repository_snapshot_id=1,
        inventory_snapshot=original,
        status="pending",
        depends_on_json="[1]",
    )
    independent = JobStep(
        job=job,
        position=3,
        name="Independent",
        playbook="other.yml",
        job_repository_snapshot_id=1,
        inventory_snapshot=original,
        status="pending",
        depends_on_json="[]",
    )
    db.session.add_all(
        [
            first,
            second,
            independent,
        ]
    )
    db.session.commit()

    return (
        inventory,
        job,
        first,
        second,
        independent,
        original,
    )


def test_refresh_creates_new_snapshot_for_pending_descendants_only(
    app,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )

    with app.app_context():
        (
            _inventory,
            job,
            first,
            second,
            independent,
            original,
        ) = _job_with_dependency_chain()

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_inventory",
            lambda _inventory, **_kwargs: _inventory_data(
                "old-host",
                "new-host",
            ),
        )

        snapshots = (
            refresh_job_inventories_after_step(
                job,
                first,
            )
        )

        assert len(snapshots) == 1

        refreshed = snapshots[0]

        assert refreshed.id != original.id
        assert refreshed.version == 2
        assert refreshed.host_count == 2

        db.session.refresh(second)
        db.session.refresh(independent)

        assert (
            second.job_inventory_snapshot_id
            == refreshed.id
        )
        assert (
            independent.job_inventory_snapshot_id
            == original.id
        )

        assert read_job_inventory_snapshot_data(
            original
        ) == _inventory_data(
            "old-host"
        )

        assert read_job_inventory_snapshot_data(
            refreshed
        ) == _inventory_data(
            "old-host",
            "new-host",
        )


def test_refresh_updates_transitive_descendants(
    app,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )

    with app.app_context():
        (
            _inventory,
            job,
            first,
            second,
            _independent,
            original,
        ) = _job_with_dependency_chain()

        third = JobStep(
            job=job,
            position=4,
            name="Validate",
            playbook="validate.yml",
            job_repository_snapshot_id=1,
            inventory_snapshot=original,
            status="pending",
            depends_on_json="[2]",
        )
        db.session.add(third)
        db.session.commit()

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_inventory",
            lambda _inventory, **_kwargs: _inventory_data(
                "new-host"
            ),
        )

        refreshed = (
            refresh_job_inventories_after_step(
                job,
                first,
            )[0]
        )

        db.session.refresh(second)
        db.session.refresh(third)

        assert (
            second.job_inventory_snapshot_id
            == refreshed.id
        )
        assert (
            third.job_inventory_snapshot_id
            == refreshed.id
        )


def test_refresh_disabled_is_noop(
    app,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )

    with app.app_context():
        (
            _inventory,
            job,
            first,
            second,
            _independent,
            original,
        ) = _job_with_dependency_chain()

        first.refresh_inventory_after = False
        db.session.commit()

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_inventory",
            lambda _inventory: (_ for _ in ()).throw(
                AssertionError(
                    "refresh should not run"
                )
            ),
        )

        assert (
            refresh_job_inventories_after_step(
                job,
                first,
            )
            == []
        )

        db.session.refresh(second)

        assert (
            second.job_inventory_snapshot_id
            == original.id
        )


def test_sliced_refresh_replans_pending_descendant_hosts_by_runner(
    app,
    monkeypatch,
    tmp_path,
):
    from datetime import datetime, timezone

    from app.models import JobStepExecutionSlice, Runner

    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )

    with app.app_context():
        (
            _inventory,
            job,
            first,
            second,
            _independent,
            _original,
        ) = _job_with_dependency_chain()

        runner = Runner(
            name="runner-a",
            hostname="runner-a.example.com",
            runner_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            enabled=True,
            is_local=False,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.flush()

        job.dispatch_target = "sliced"
        stale_slice = JobStepExecutionSlice(
            step=second,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status="pending",
        )
        stale_slice.set_hosts(["old-host"])
        stale_slice.set_required_capabilities(["ansible"])
        db.session.add(stale_slice)
        db.session.commit()

        refreshed_data = _inventory_data(
            "old-host",
            "new-host",
        )
        refreshed_data["_meta"]["hostvars"]["new-host"] = {
            "foreman_params": {
                "journeyman_runner": "runner-a.example.com",
            },
        }

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_inventory",
            lambda _inventory, **_kwargs: refreshed_data,
        )
        monkeypatch.setattr(
            job_inventory_refresh,
            "target_hosts_for_inventory",
            lambda inventory_data, _limit="": tuple(
                sorted(
                    inventory_data["_meta"]["hostvars"]
                )
            ),
        )

        refresh_job_inventories_after_step(
            job,
            first,
        )

        db.session.refresh(second)
        slices = list(second.execution_slices)

        assert len(slices) == 2
        assert slices[0].dispatch_target == "local"
        assert slices[0].get_hosts() == ["old-host"]
        assert slices[1].dispatch_target == "remote"
        assert slices[1].required_runner_id == runner.id
        assert slices[1].get_hosts() == ["new-host"]
        assert all(item.status == "pending" for item in slices)


def test_sliced_refresh_rejects_replan_after_descendant_slice_dispatch(
    app,
    monkeypatch,
    tmp_path,
):
    from app.models import JobStepExecutionSlice
    from app.services.job_inventory_refresh import JobInventoryRefreshError

    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )

    with app.app_context():
        (
            _inventory,
            job,
            first,
            second,
            _independent,
            original,
        ) = _job_with_dependency_chain()

        job.dispatch_target = "sliced"
        started_slice = JobStepExecutionSlice(
            step=second,
            position=1,
            dispatch_target="local",
            runner_name="local",
            runner_hostname="localhost",
            status="running",
        )
        started_slice.set_hosts(["old-host"])
        started_slice.set_required_capabilities(["ansible"])
        db.session.add(started_slice)
        db.session.commit()

        monkeypatch.setattr(
            job_inventory_refresh,
            "refresh_inventory",
            lambda _inventory, **_kwargs: _inventory_data("new-host"),
        )

        try:
            refresh_job_inventories_after_step(
                job,
                first,
            )
        except JobInventoryRefreshError as exc:
            assert "already been dispatched" in str(exc)
        else:
            raise AssertionError("Expected refresh/replan to be rejected")

        db.session.refresh(second)
        assert second.job_inventory_snapshot_id == original.id
        assert second.execution_slices[0].status == "running"
