"""Read-only presentation helpers for an execution Oversight page."""

from app.services.job_inventory_snapshot import (
    JobInventorySnapshotError,
    read_job_inventory_snapshot_data,
)
from app.services.project_oversight import oversight_candidates


def _inventory_hosts(snapshot):
    if snapshot is None:
        return [], "No inventory snapshot is associated with this step."

    try:
        data = read_job_inventory_snapshot_data(snapshot)
    except JobInventorySnapshotError as exc:
        return [], str(exc)

    hostvars = data.get("_meta", {}).get("hostvars", {})
    return sorted(str(host) for host in hostvars), ""


def build_oversight_rows(job):
    rows = []

    for step in oversight_candidates(job):
        inventory_hosts, inventory_error = _inventory_hosts(
            step.inventory_snapshot
        )
        repository = step.repository_snapshot
        destinations = []

        for execution_slice in step.execution_slices:
            label = (
                "Built-in local runner"
                if execution_slice.dispatch_target == "local"
                else (
                    execution_slice.runner_hostname
                    or execution_slice.runner_name
                    or "Remote runner"
                )
            )
            destinations.append({
                "label": label,
                "host_count": len(execution_slice.get_hosts()),
            })

        rows.append({
            "step": step,
            "inventory_hosts": inventory_hosts,
            "inventory_error": inventory_error,
            "repository": repository,
            "destinations": destinations,
            "credentials": list(step.credential_snapshots),
            "dependency_positions": step.get_dependency_positions(),
        })

    return rows
