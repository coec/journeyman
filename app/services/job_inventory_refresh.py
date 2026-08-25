"""Mid-workflow refresh of immutable Job inventory snapshots."""

from app import db
from app.models import Inventory, JobInventorySnapshot
from app.services.inventory_resolver import (
    InventoryResolutionError,
    refresh_inventory,
)
from app.services.job_inventory_snapshot import (
    JobInventorySnapshotError,
    delete_job_inventory_snapshot_path,
    write_job_inventory_snapshot,
)
from app.services.execution_slices import (
    materialize_step_execution_slices,
    plan_step_execution_slices,
)
from app.services.execution_target_hosts import (
    ExecutionTargetResolutionError,
    target_hosts_for_inventory,
)
from app.services.inventory_runner_routing import (
    InventoryRunnerRoutingError,
)
from app.services.runner_environments import job_step_environment_requirement


class JobInventoryRefreshError(Exception):
    """A Job could not safely refresh inventories between workflow steps."""


def _dependency_ancestors(step, steps_by_position):
    result = set()
    stack = list(step.get_dependency_positions())

    while stack:
        position = int(stack.pop())

        if position in result:
            continue

        result.add(position)

        dependency = steps_by_position.get(position)

        if dependency is not None:
            stack.extend(
                dependency.get_dependency_positions()
            )

    return result


def _target_steps(job, trigger_step):
    steps_by_position = {
        step.position: step
        for step in job.steps
    }

    return [
        step
        for step in job.steps
        if (
            step.status == "pending"
            and trigger_step.position
            in _dependency_ancestors(
                step,
                steps_by_position,
            )
            and step.inventory_snapshot is not None
            and step.inventory_snapshot.inventory_id is not None
        )
    ]



def _required_capabilities(step, job):
    capabilities = set()

    for execution_slice in step.execution_slices:
        capabilities.update(
            execution_slice.get_required_capabilities()
        )

    if capabilities:
        return sorted(capabilities)

    return [
        "shell"
        if job.execution_type == "shell"
        else "ansible"
    ]


def _replan_pending_execution_slices(
    job,
    targets,
    refreshed_inventory_data,
):
    """Replace pending descendant slices using refreshed inventory data.

    Planning is completed for every target before any existing slice rows are
    replaced.  If one refreshed host references an invalid runner, no pending
    descendant receives a partial new plan.
    """

    if job.dispatch_target != "sliced":
        return

    plans_by_step_id = {}

    for step in targets:
        if step.status != "pending":
            raise JobInventoryRefreshError(
                "Cannot replan step {} because it is no longer pending."
                .format(step.position)
            )

        if any(
            execution_slice.status != "pending"
            for execution_slice in step.execution_slices
        ):
            raise JobInventoryRefreshError(
                "Cannot replan step {} because one of its execution slices "
                "has already been dispatched.".format(step.position)
            )

        inventory_id = step.inventory_snapshot.inventory_id
        inventory_data = refreshed_inventory_data.get(inventory_id)

        if inventory_data is None:
            raise JobInventoryRefreshError(
                "Refreshed inventory data for step {} is unavailable."
                .format(step.position)
            )

        try:
            target_hosts = target_hosts_for_inventory(
                inventory_data,
                step.limit or "",
            )
            capabilities = _required_capabilities(step, job)
            plans = plan_step_execution_slices(
                inventory_data=inventory_data,
                target_hosts=target_hosts,
                default_runner=job.default_runner,
                default_runner_crew=job.default_runner_crew,
                required_capabilities=capabilities,
                required_environment=(
                    job_step_environment_requirement(step)
                    if job.execution_type != "shell"
                    else None
                ),
            )
        except (
            ExecutionTargetResolutionError,
            InventoryRunnerRoutingError,
        ) as exc:
            raise JobInventoryRefreshError(
                "Runner routing replan failed for step {}: {}"
                .format(step.position, exc)
            ) from exc

        plans_by_step_id[step.id] = (
            plans,
            capabilities,
        )

    # Delete the old pending rows and flush those DELETEs before inserting
    # replacement slices.  The table has a unique (job_step_id, position)
    # constraint, and SQLAlchemy may otherwise INSERT position 1 before the
    # delete-orphan for the old position 1 has reached the database.
    for step in targets:
        step.execution_slices.clear()

    db.session.flush()

    for step in targets:
        plans, capabilities = plans_by_step_id[step.id]
        materialize_step_execution_slices(
            job_step=step,
            plans=plans,
            required_capabilities=capabilities,
        )

def refresh_job_inventories_after_step(job, trigger_step):
    """
    Re-resolve inventories for pending descendants of a successful step.

    Existing Job snapshots are never modified. New immutable versions are
    created and only dependent pending steps are repointed to those versions.
    """

    if not trigger_step.refresh_inventory_after:
        return []

    targets = _target_steps(
        job,
        trigger_step,
    )

    if not targets:
        return []

    inventory_ids = []

    for step in targets:
        inventory_id = (
            step.inventory_snapshot.inventory_id
        )

        if inventory_id not in inventory_ids:
            inventory_ids.append(
                inventory_id
            )

    next_version = max(
        (
            snapshot.version
            for snapshot in job.inventory_snapshots
        ),
        default=0,
    ) + 1

    created = []
    created_paths = []
    refreshed_inventory_data = {}

    try:
        for inventory_id in inventory_ids:
            inventory = db.session.get(
                Inventory,
                inventory_id,
            )

            if inventory is None:
                raise JobInventoryRefreshError(
                    "Inventory {} no longer exists."
                    .format(inventory_id)
                )

            if not inventory.enabled:
                raise JobInventoryRefreshError(
                    'Inventory "{}" is disabled.'
                    .format(inventory.name)
                )

            try:
                inventory_data = refresh_inventory(
                    inventory,
                    bindings=(
                        job.package_snapshot.get_inventory_bindings()
                        if job.package_snapshot is not None
                        else None
                    ),
                )
            except InventoryResolutionError as exc:
                raise JobInventoryRefreshError(
                    'Unable to refresh inventory "{}": {}'
                    .format(
                        inventory.name,
                        exc,
                    )
                ) from exc

            snapshot = JobInventorySnapshot(
                job=job,
                inventory_id=inventory.id,
                inventory_name=inventory.name,
                inventory_type=inventory.inventory_type,
                version=next_version,
            )
            next_version += 1

            db.session.add(snapshot)
            db.session.flush()

            try:
                path = write_job_inventory_snapshot(
                    snapshot,
                    inventory_data,
                )
            except JobInventorySnapshotError as exc:
                raise JobInventoryRefreshError(
                    'Unable to snapshot refreshed inventory "{}".'
                    .format(inventory.name)
                ) from exc

            created_paths.append(path)
            refreshed_inventory_data[inventory.id] = inventory_data

            for step in targets:
                if (
                    step.inventory_snapshot is not None
                    and step.inventory_snapshot.inventory_id
                    == inventory.id
                ):
                    step.inventory_snapshot = snapshot

            created.append(snapshot)

        _replan_pending_execution_slices(
            job,
            targets,
            refreshed_inventory_data,
        )

        db.session.commit()
        return created

    except Exception:
        db.session.rollback()

        for path in reversed(created_paths):
            try:
                delete_job_inventory_snapshot_path(
                    path
                )
            except JobInventorySnapshotError:
                pass

        raise
