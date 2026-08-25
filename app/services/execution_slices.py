"""Plan per-host runner slices for one Job step.

This module is intentionally planning-only in the first fan-out patch.  The
existing Job dispatcher remains authoritative until slice claiming/execution is
switched over in the following patch.
"""

from dataclasses import dataclass

from app.services.inventory_runner_routing import (
    InventoryRunnerRoutingError,
    _is_local_target,
    _runner_reference,
    registered_remote_runner,
)
from app.services.runner_crews import (
    RunnerCrewSelectionError,
    select_crew_runner,
)
from app.services.runner_environments import (
    RunnerEnvironmentUnavailable,
    require_runner_environment,
)


@dataclass(frozen=True)
class StepExecutionSlicePlan:
    dispatch_target: str
    required_runner_id: int | None
    runner_name: str
    runner_hostname: str
    hosts: tuple[str, ...]

    @property
    def host_count(self):
        return len(self.hosts)


def _hostvars(inventory_data):
    if not isinstance(inventory_data, dict):
        raise InventoryRunnerRoutingError("Resolved inventory is invalid.")
    value = inventory_data.get("_meta", {}).get("hostvars")
    if not isinstance(value, dict):
        raise InventoryRunnerRoutingError(
            "Resolved inventory has no hostvars mapping."
        )
    return value


def plan_step_execution_slices(
    *,
    inventory_data,
    target_hosts,
    default_runner,
    default_runner_crew=None,
    required_capabilities=None,
    required_environment=None,
    additional_runner_loads=None,
):
    """Group one step's effective hosts by their effective Journeyman runner.

    Precedence:

      1. localhost / ansible_connection=local -> built-in local runner
      2. explicit host journeyman_runner       -> that registered runner
      3. Project default exact runner          -> that runner
      4. Project default Runner Crew           -> least-busy eligible member
      5. no Project default                    -> built-in local runner

    A Runner Crew is resolved once for the whole default-routed host set in the
    step.  Hosts are not randomly spread across crew members.
    """

    hostvars = _hostvars(inventory_data)
    groups = {}
    crew_hosts = []

    def add_remote(runner, host):
        required = {
            str(item).strip().lower()
            for item in (required_capabilities or [])
            if str(item).strip()
        }
        if not required.issubset(runner.capabilities()):
            raise InventoryRunnerRoutingError(
                'Runner "{}" does not provide required execution capabilities: {}.'
                .format(runner.name, ", ".join(sorted(required)))
            )
        if required_environment is not None:
            try:
                require_runner_environment(runner, required_environment)
            except RunnerEnvironmentUnavailable as exc:
                raise InventoryRunnerRoutingError(str(exc)) from exc
        key = ("remote", runner.id)
        groups.setdefault(
            key,
            {
                "dispatch_target": "remote",
                "required_runner_id": runner.id,
                "runner_name": str(runner.name or ""),
                "runner_hostname": str(runner.hostname or ""),
                "hosts": [],
            },
        )["hosts"].append(host)

    def add_local(host):
        key = ("local", None)
        groups.setdefault(
            key,
            {
                "dispatch_target": "local",
                "required_runner_id": None,
                "runner_name": "local",
                "runner_hostname": "localhost",
                "hosts": [],
            },
        )["hosts"].append(host)

    for host in sorted({str(item) for item in target_hosts}):
        variables = hostvars.get(host, {})
        if not isinstance(variables, dict):
            variables = {}

        if _is_local_target(host, variables):
            add_local(host)
            continue

        reference = _runner_reference(variables)
        if reference:
            runner = registered_remote_runner(reference)
            if runner is None:
                raise InventoryRunnerRoutingError(
                    'Host "{}" requests runner "{}", but no enabled registered '
                    "remote runner with that name, hostname or UUID exists."
                    .format(host, reference)
                )
            add_remote(runner, host)
            continue

        if default_runner is not None:
            add_remote(default_runner, host)
            continue

        if default_runner_crew is not None:
            crew_hosts.append(host)
            continue

        add_local(host)

    if crew_hosts:
        try:
            runner = select_crew_runner(
                default_runner_crew,
                required_capabilities=required_capabilities,
                required_environment=required_environment,
                additional_loads=additional_runner_loads,
            )
        except RunnerCrewSelectionError as exc:
            raise InventoryRunnerRoutingError(str(exc)) from exc
        for host in crew_hosts:
            add_remote(runner, host)

    def sort_key(item):
        (_key, value) = item
        return (
            0 if value["dispatch_target"] == "local" else 1,
            value["runner_name"].lower(),
            value["runner_hostname"].lower(),
            value["required_runner_id"] or 0,
        )

    return tuple(
        StepExecutionSlicePlan(
            dispatch_target=value["dispatch_target"],
            required_runner_id=value["required_runner_id"],
            runner_name=value["runner_name"],
            runner_hostname=value["runner_hostname"],
            hosts=tuple(sorted(value["hosts"])),
        )
        for _key, value in sorted(groups.items(), key=sort_key)
    )


def materialize_step_execution_slices(*, job_step, plans, required_capabilities):
    """Attach immutable pending slice rows to a JobStep snapshot."""

    from app.models import JobStepExecutionSlice

    job_step.execution_slices.clear()

    for position, plan in enumerate(plans, start=1):
        execution_slice = JobStepExecutionSlice(
            position=position,
            dispatch_target=plan.dispatch_target,
            required_runner_id=plan.required_runner_id,
            runner_name=plan.runner_name,
            runner_hostname=plan.runner_hostname,
            status="pending",
        )
        execution_slice.set_hosts(plan.hosts)
        execution_slice.set_required_capabilities(required_capabilities)
        job_step.execution_slices.append(execution_slice)

    return tuple(job_step.execution_slices)
