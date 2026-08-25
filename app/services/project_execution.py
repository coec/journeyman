"""
Creation of immutable queued Project executions.

All entry points that execute a Project should eventually call
queue_project_execution():

    - normal Project runs
    - interactive re-runs
    - scheduled runs
    - Schedule "Run now"
    - future API launches

This module creates database snapshots and immutable filesystem
inventory snapshots. It does not execute Ansible.
"""

from datetime import datetime, timezone

from flask import current_app

from .. import db
from ..models import (
    Credential,
    Job,
    JobCredentialSnapshot,
    JobInventorySnapshot,
    JobPackageSnapshot,
    JobRepositorySnapshot,
    JobStep,
    Runner,
)
from .inventory_cache import inventory_host_count
from .environments import default_environment
from .runner_environments import environment_requirement
from .inventory_resolver import (
    InventoryResolutionError,
    resolve_inventory,
)
from .inventory_runner_routing import (
    InventoryRunnerRoutingError,
    derive_inventory_runner_routing,
    validate_default_runner,
    validate_default_runner_crew,
    validate_inventory_runner_overrides,
)
from .execution_slices import (
    materialize_step_execution_slices,
    plan_step_execution_slices,
)
from .execution_target_hosts import (
    ExecutionTargetResolutionError,
    target_hosts_for_inventory,
)
from .job_inventory_snapshot import (
    JobInventorySnapshotError,
    delete_job_inventory_snapshot_path,
    write_job_inventory_snapshot,
)
from .project_package_execution import (
    PackageExecutionData,
)
from ..credential_types import CREDENTIAL_TYPE_MACHINE
from .project_repositories import (
    ProjectRepositoryRefreshError,
    refresh_project_repositories,
)
from .project_concurrency import (
    launch_blocking_job,
    locked_project,
    normalise_concurrency_policy,
    parameter_signature,
    project_concurrency_message,
)


class ProjectExecutionQueueError(Exception):
    """
    A Project could not be converted into a queued immutable Job.

    The exception message is safe to show in the web interface.
    """


def _utcnow():
    return datetime.now(timezone.utc)


def _repository_commit(repository):
    """
    Return the synchronized Git commit recorded by Repository.

    The repository implementation currently records the synchronized
    revision in last_commit. The additional names preserve compatibility
    with older Journeyman development databases.
    """

    for attribute in (
        "commit_sha",
        "current_commit",
        "last_commit",
        "revision",
    ):
        value = getattr(
            repository,
            attribute,
            None,
        )

        if value:
            return str(value)

    return None


def _remove_abandoned_snapshot_paths(snapshot_paths):
    """
    Best-effort cleanup after a database or filesystem failure.
    """

    for snapshot_path in reversed(snapshot_paths):
        try:
            delete_job_inventory_snapshot_path(
                snapshot_path
            )

        except JobInventorySnapshotError:
            current_app.logger.exception(
                "Unable to remove abandoned inventory "
                "snapshot %s",
                snapshot_path,
            )


def queue_project_execution(
    *,
    project,
    requested_by,
    message="Queued from the Journeyman web interface.",
    resolved_inventory_data=None,
    package_execution=None,
    progress=None,
):
    """
    Create and commit one immutable queued Job for a Project.

    The Project's current enabled steps, repository revisions,
    credentials, inventories and execution settings are snapshotted.

    Returns the committed Job.

    Raises:
        ProjectExecutionQueueError:
            The Project cannot be queued safely.
    """

    requested_by = str(
        requested_by or ""
    ).strip()

    if not requested_by:
        raise ProjectExecutionQueueError(
            "No authenticated username was supplied to Journeyman."
        )

    if progress is not None:
        progress("validate", "Validating dispatch configuration")

    if not project.enabled:
        raise ProjectExecutionQueueError(
            "This project is disabled and cannot be run."
        )

    if (
        any(getattr(step, "oversight_after", False) for step in project.steps)
        and requested_by.startswith("reactor:")
    ):
        raise ProjectExecutionQueueError(
            "Projects requiring oversight cannot be launched automatically "
            "as Reactions."
        )

    if (
        package_execution is not None
        and not isinstance(
            package_execution,
            PackageExecutionData,
        )
    ):
        raise ProjectExecutionQueueError(
            "Invalid Project Package execution data."
        )

    project = locked_project(project)
    try:
        concurrency_policy = normalise_concurrency_policy(project.concurrency_policy)
    except ValueError as exc:
        raise ProjectExecutionQueueError(str(exc)) from exc
    concurrency_signature = parameter_signature(package_execution)
    blocker = launch_blocking_job(
        project, concurrency_policy, concurrency_signature
    )
    if blocker is not None:
        raise ProjectExecutionQueueError(
            project_concurrency_message(project, concurrency_policy, blocker)
        )

    machine_credential_override = None
    if (
        package_execution is not None
        and package_execution.machine_credential_override_id is not None
    ):
        machine_credential_override = db.session.get(
            Credential,
            package_execution.machine_credential_override_id,
        )
        if (
            machine_credential_override is None
            or machine_credential_override.credential_type != CREDENTIAL_TYPE_MACHINE
        ):
            raise ProjectExecutionQueueError(
                "The selected Machine credential override is no longer available."
            )
        if machine_credential_override.encrypted_data is None:
            raise ProjectExecutionQueueError(
                'Credential "{}" has no stored secret data.'.format(
                    machine_credential_override.name
                )
            )

    def effective_step_credentials(project_step):
        values = list(project_step.effective_credentials())
        if machine_credential_override is None:
            return values
        values = [
            credential
            for credential in values
            if credential.credential_type != CREDENTIAL_TYPE_MACHINE
        ]
        values.append(machine_credential_override)
        return values

    project_steps = sorted(
        (
            step
            for step in project.steps
            if step.enabled
        ),
        key=lambda step: step.position,
    )

    if not project_steps:
        raise ProjectExecutionQueueError(
            "This project has no enabled workflow steps."
        )

    execution_type = project.execution_type or "ansible"
    required_capability = (
        "shell" if execution_type == "shell" else "ansible"
    )

    # Interactive launches refresh before preview. Non-interactive callers
    # such as schedules may queue without preview, so refresh here as well.
    if resolved_inventory_data is None:
        if progress is not None:
            progress("repository", "Synchronizing Project repositories")
        try:
            refresh_project_repositories(project)
        except ProjectRepositoryRefreshError as exc:
            raise ProjectExecutionQueueError(str(exc)) from exc

    repositories = {}
    credentials = {}
    effective_inventories = {}
    step_effective_inventory_ids = {}
    step_environments = {}
    step_environment_requirements = {}

    #
    # Validate every step before creating any Job objects.
    #
    for position, project_step in enumerate(
        project_steps,
        start=1,
    ):
        step_name = (
            project_step.name
            or "Step {}".format(position)
        )

        environment = project_step.effective_environment() or default_environment()
        if environment is None or not environment.enabled:
            raise ProjectExecutionQueueError(
                'Step {} "{}" has no enabled execution environment.'.format(position, step_name)
            )
        if environment.validation_status != "passed":
            raise ProjectExecutionQueueError(
                'Execution environment "{}" must pass validation before use.'.format(environment.name)
            )
        step_environments[project_step.id] = environment
        step_environment_requirements[project_step.id] = environment_requirement(
            environment
        )

        repository = project_step.effective_repository()

        if repository is None:
            raise ProjectExecutionQueueError(
                'Step "{}" has no repository.'
                .format(step_name)
            )

        if repository.status != "up_to_date":
            raise ProjectExecutionQueueError(
                'Repository "{}" must be synchronized.'
                .format(repository.name)
            )

        repository_commit = _repository_commit(
            repository
        )

        if not repository_commit:
            raise ProjectExecutionQueueError(
                'Repository "{}" has no recorded synchronized '
                "commit."
                .format(repository.name)
            )

        repositories.setdefault(
            repository.id,
            (
                repository,
                repository_commit,
            ),
        )

        inventory = (
            project_step.inventory
            or project.inventory
        )

        if inventory is None:
            raise ProjectExecutionQueueError(
                'Step {} "{}" has no effective inventory.'
                .format(
                    position,
                    step_name,
                )
            )

        if not inventory.enabled:
            raise ProjectExecutionQueueError(
                'Step {} inventory "{}" is disabled.'
                .format(
                    position,
                    inventory.name,
                )
            )

        step_effective_inventory_ids[
            project_step.id
        ] = inventory.id

        # Dict insertion ordering preserves first-use ordering and
        # therefore stable inventory snapshot version numbers.
        effective_inventories.setdefault(
            inventory.id,
            inventory,
        )

        for credential in effective_step_credentials(project_step):
            if credential.encrypted_data is None:
                raise ProjectExecutionQueueError(
                    'Credential "{}" has no stored secret data.'
                    .format(credential.name)
                )

            credentials.setdefault(
                credential.id,
                credential,
            )

    #
    # Resolve each unique effective inventory exactly once.
    #
    if resolved_inventory_data is None:
        resolved_inventory_data = {}

        for inventory_id, inventory in (
            effective_inventories.items()
        ):
            if progress is not None:
                progress(
                    "inventory",
                    'Resolving inventory "{}"'.format(inventory.name),
                )
            try:
                resolved_inventory_data[
                    inventory_id
                ] = resolve_inventory(
                    inventory,
                    bindings=(
                        package_execution.inventory_bindings
                        if package_execution is not None
                        else None
                    ),
                )

            except InventoryResolutionError as exc:
                current_app.logger.warning(
                    "Unable to resolve Inventory %s "
                    "for Project %s: %s",
                    inventory.id,
                    project.id,
                    exc,
                )

                raise ProjectExecutionQueueError(
                    'Unable to resolve inventory "{}": {}'
                    .format(
                        inventory.name,
                        exc,
                    )
                ) from exc

    else:
        resolved_inventory_data = dict(
            resolved_inventory_data
        )

        expected_inventory_ids = set(
            effective_inventories
        )

        provided_inventory_ids = set(
            resolved_inventory_data
        )

        if (
            provided_inventory_ids
            != expected_inventory_ids
        ):
            raise ProjectExecutionQueueError(
                "The confirmed inventory data no longer matches "
                "the Project's effective inventories."
            )


    # Validate runner routing only after every currently resolvable inventory
    # has been collected, but before any Job rows or filesystem snapshots are
    # created. Later workflow inventory refreshes will repeat this validation
    # before newly discovered hosts are dispatched.
    if progress is not None:
        progress("routing", "Validating runner routing")

    try:
        default_runner = validate_default_runner(project)
        default_runner_crew = validate_default_runner_crew(project)
        if default_runner is not None and default_runner_crew is not None:
            raise InventoryRunnerRoutingError(
                "A Project cannot select both a default runner and a default Runner Crew."
            )
        # Validate every explicit reference in every currently resolvable
        # inventory, even if a particular step limit does not select that host.
        # A stale/invalid Journeyman routing directive is a configuration error.
        validate_inventory_runner_overrides(resolved_inventory_data)
    except InventoryRunnerRoutingError as exc:
        raise ProjectExecutionQueueError(
            "Runner routing preflight failed: {}".format(exc)
        ) from exc

    required_capabilities = '["{}"]'.format(required_capability)

    # Resolve the actual hosts selected by each step/limit and build immutable
    # slice plans now.  Jobs with more than one execution target are dispatched
    # as independent local/remote slices.  Mid-workflow inventory refresh still
    # remains a safety boundary until dynamic slice replanning is implemented.
    step_slice_plans = {}
    legacy_execution_targets = set()
    planned_runner_loads = {}

    if progress is not None:
        progress("routing", "Planning execution destinations")

    for project_step in project_steps:
        inventory_id = step_effective_inventory_ids[project_step.id]
        effective_limit = (
            package_execution.step_limit
            if (
                package_execution is not None
                and package_execution.step_limit
            )
            else project_step.limit or ""
        )

        try:
            target_hosts = target_hosts_for_inventory(
                resolved_inventory_data[inventory_id],
                effective_limit,
            )
            plans = plan_step_execution_slices(
                inventory_data=resolved_inventory_data[inventory_id],
                target_hosts=target_hosts,
                default_runner=default_runner,
                default_runner_crew=default_runner_crew,
                required_capabilities={required_capability},
                required_environment=(
                    step_environment_requirements[project_step.id]
                    if required_capability == "ansible"
                    else None
                ),
                additional_runner_loads=planned_runner_loads,
            )
        except (
            ExecutionTargetResolutionError,
            InventoryRunnerRoutingError,
        ) as exc:
            raise ProjectExecutionQueueError(
                "Runner routing preflight failed for step {}: {}".format(
                    project_step.position,
                    exc,
                )
            ) from exc

        step_slice_plans[project_step.id] = plans
        legacy_execution_targets.update(
            (plan.dispatch_target, plan.required_runner_id)
            for plan in plans
        )
        for plan in plans:
            if plan.dispatch_target == "remote" and plan.required_runner_id is not None:
                planned_runner_loads[plan.required_runner_id] = (
                    planned_runner_loads.get(plan.required_runner_id, 0) + 1
                )

    uses_midworkflow_refresh = any(
        step.refresh_inventory_after
        for step in project_steps
    )

    routing = project.runner_routing or "local"

    # Every resolved execution target is represented and executed as a slice,
    # even when the Job uses only one runner.  Keeping a single execution model
    # prevents placeholder slices from remaining pending while a legacy direct
    # Job path performs the real work and ensures live output/status are always
    # attached to the execution slice shown in the Job UI.
    if legacy_execution_targets:
        dispatch_target = "sliced"
        required_runner_id = None
        required_runner_site = ""
    elif default_runner is not None:
        dispatch_target = "remote"
        required_runner_site = ""
        required_runner_id = default_runner.id
    elif default_runner_crew is not None:
        from .runner_crews import RunnerCrewSelectionError, select_crew_runner
        try:
            selected_runner = select_crew_runner(
                default_runner_crew,
                required_capabilities={required_capability},
            )
        except RunnerCrewSelectionError as exc:
            raise ProjectExecutionQueueError(str(exc)) from exc
        dispatch_target = "remote"
        required_runner_site = ""
        required_runner_id = selected_runner.id
    elif routing == "inventory":
        # Compatibility for an empty-target legacy inventory-routed Project.
        # Non-empty target sets are represented by the slice plans above.
        try:
            inventory_routing = derive_inventory_runner_routing(
                resolved_inventory_data
            )
        except InventoryRunnerRoutingError as exc:
            raise ProjectExecutionQueueError(str(exc)) from exc

        dispatch_target = inventory_routing["dispatch_target"]
        required_runner_site = inventory_routing["required_runner_site"]
        required_runner_id = inventory_routing["required_runner_id"]
    else:
        dispatch_target = "local" if routing == "local" else "remote"
        required_runner_site = (
            project.runner_site or ""
            if routing == "remote_site"
            else ""
        )
        required_runner_id = (
            project.runner_id
            if routing == "remote_runner"
            else None
        )

    oversight_after_positions = {
        step.position
        for step in project_steps
        if getattr(step, "oversight_after", False)
    }
    project_has_oversight = bool(oversight_after_positions)

    if project_has_oversight:
        # Oversight must retain server-side control between workflow steps.
        # Per-step execution slices provide that control for local and remote
        # destinations alike; whole-Job remote dispatch cannot pause safely
        # between steps once handed to a runner.
        dispatch_target = "sliced"
        required_runner_site = ""
        required_runner_id = None

    if progress is not None:
        progress("snapshot", "Snapshotting execution configuration")

    job = Job(
        project_id=project.id,
        project_name=project.name,
        status="queued",
        requested_by=requested_by,
        execution_type=project.execution_type or "ansible",
        max_parallel_steps=max(1, min(32, project.max_parallel_steps or 4)),
        concurrency_policy=concurrency_policy,
        concurrency_signature=concurrency_signature,
        oversight_required_between_all_steps=project_has_oversight,
        oversight_reviewer=requested_by,
        queued_at=_utcnow(),
        message=message,
        dispatch_target=dispatch_target,
        required_runner_site=required_runner_site,
        required_runner_id=required_runner_id,
        default_runner_id=(default_runner.id if default_runner is not None else None),
        default_runner_crew_id=(
            default_runner_crew.id if default_runner_crew is not None else None
        ),
        required_runner_capabilities_json=required_capabilities,
    )

    if package_execution is not None:
        package_snapshot = JobPackageSnapshot(
            package_id=(
                package_execution.package_id
            ),
            package_name=(
                package_execution.package_name
            ),
            package_owner=(
                package_execution.package_owner
            ),
            step_limit=(
                package_execution.step_limit
            ),
        )

        package_snapshot.set_package_definition(
            package_execution.definition
        )

        package_snapshot.set_display_values(
            package_execution.display_values
        )

        package_snapshot.set_operational_targets(
            package_execution.operational_targets
        )

        package_snapshot.set_inventory_bindings(
            package_execution.inventory_bindings
        )

        package_snapshot.set_execution_vars(
            package_execution.execution_vars
        )

        job.package_snapshot = (
            package_snapshot
        )

    #
    # Inventory snapshots.
    #
    inventory_snapshots = {}

    for version, (
        inventory_id,
        inventory,
    ) in enumerate(
        effective_inventories.items(),
        start=1,
    ):
        inventory_data = (
            resolved_inventory_data[
                inventory_id
            ]
        )

        inventory_snapshot = JobInventorySnapshot(
            inventory_id=inventory.id,
            inventory_name=inventory.name,
            inventory_type=(
                inventory.inventory_type
            ),
            version=version,
            host_count=inventory_host_count(
                inventory_data
            ),
        )

        job.inventory_snapshots.append(
            inventory_snapshot
        )

        inventory_snapshots[
            inventory_id
        ] = inventory_snapshot

    #
    # Repository snapshots.
    #
    repository_snapshots = {}

    for repository_id, (
        repository,
        repository_commit,
    ) in repositories.items():
        repository_snapshot = (
            JobRepositorySnapshot(
                repository_id=repository.id,
                repository_name=repository.name,
                repository_url=repository.url,
                repository_commit=(
                    repository_commit
                ),
                repository_commit_message=(
                    repository.last_commit_message or ""
                ),
                repository_commit_author=(
                    repository.last_commit_author or ""
                ),
                repository_commit_at=(
                    repository.last_commit_at
                ),
            )
        )

        job.repository_snapshots.append(
            repository_snapshot
        )

        repository_snapshots[
            repository_id
        ] = repository_snapshot

    #
    # Credential snapshots.
    #
    credential_snapshots = {}

    for credential_id, credential in (
        credentials.items()
    ):
        credential_snapshot = (
            JobCredentialSnapshot(
                credential_id=credential.id,
                credential_name=credential.name,
                credential_owner=credential.owner,
                credential_type=(
                    credential.credential_type
                ),
                username=credential.username or "",
                encrypted_data=(
                    credential.encrypted_data
                ),
                secret_format_version=(
                    credential.secret_format_version
                ),
                credential_key_id=(
                    credential.credential_key_id
                ),
            )
        )

        job.credential_snapshots.append(
            credential_snapshot
        )

        credential_snapshots[
            credential_id
        ] = credential_snapshot

    #
    # Check mode is a Project-wide execution setting.  Older Projects may
    # still contain a mixture of per-step values from the initial v1.3.0
    # implementation, so treat any enabled stored step value as enabling
    # check mode for every Ansible step in this execution.  Saving the
    # Project through the current editor normalises all step values.
    #
    project_check_mode = (
        project.execution_type == "ansible"
        and any(bool(step.check_mode) for step in project_steps)
    )

    #
    # Workflow-step snapshots.
    #
    for position, project_step in enumerate(
        project_steps,
        start=1,
    ):
        repository_snapshot = (
            repository_snapshots[
                project_step.effective_repository().id
            ]
        )

        inventory_id = (
            step_effective_inventory_ids[
                project_step.id
            ]
        )

        inventory_snapshot = (
            inventory_snapshots[
                inventory_id
            ]
        )

        step_credential_snapshots = [
            credential_snapshots[
                credential.id
            ]
            for credential
            in effective_step_credentials(project_step)
        ]

        execution_environment = step_environments[project_step.id]

        job_step = JobStep(
                project_step_id=project_step.id,
                repository_snapshot=(
                    repository_snapshot
                ),
                inventory_snapshot=(
                    inventory_snapshot
                ),
                credential_snapshots=(
                    step_credential_snapshots
                ),
                position=position,
                name=(
                    project_step.name
                    or "Step {}".format(position)
                ),
                environment_name=execution_environment.name,
                environment_id=execution_environment.id,
                environment_revision=(
                    step_environment_requirements[project_step.id].revision
                ),
                environment_path=execution_environment.path,
                ansible_config_path=(
                    execution_environment.ansible_config_path
                    or "/etc/ansible/ansible.cfg"
                ),
                playbook=project_step.playbook,
                limit=(
                    package_execution.step_limit
                    if (
                        package_execution is not None
                        and package_execution.step_limit
                    )
                    else project_step.limit or ""
                ),
                tags=project_step.tags or "",
                skip_tags=(
                    project_step.skip_tags
                    or ""
                ),
                extra_vars_json=project_step.extra_vars_json or "{}",
                verbosity=project_step.verbosity,
                check_mode=project_check_mode,
                remote_shell_become=project_step.remote_shell_become,
                remote_shell_serial=project_step.remote_shell_serial,
                continue_on_failure=(
                    project_step.continue_on_failure
                ),
                failure_only=project_step.failure_only,
                refresh_inventory_after=(
                    project_step.refresh_inventory_after
                ),
                depends_on_json=project_step.depends_on_json or "[]",
                oversight_required_before=any(
                    dependency in oversight_after_positions
                    for dependency in project_step.get_dependency_positions()
                ),
                oversight_approved=not any(
                    dependency in oversight_after_positions
                    for dependency in project_step.get_dependency_positions()
                ),
                status="pending",
            )

        materialize_step_execution_slices(
            job_step=job_step,
            plans=step_slice_plans[project_step.id],
            required_capabilities=[required_capability],
        )

        job.steps.append(job_step)

    db.session.add(job)

    snapshot_paths = []

    try:
        if progress is not None:
            progress("snapshot", "Writing immutable inventory snapshots")

        # The Job and its inventory snapshots require database IDs
        # before immutable filesystem paths can be constructed.
        db.session.flush()

        for inventory_id, inventory_snapshot in (
            inventory_snapshots.items()
        ):
            snapshot_path = (
                write_job_inventory_snapshot(
                    inventory_snapshot,
                    resolved_inventory_data[
                        inventory_id
                    ],
                )
            )

            snapshot_paths.append(
                snapshot_path
            )

        if progress is not None:
            progress("queue", "Queuing Job")

        db.session.commit()

    except Exception as exc:
        db.session.rollback()

        _remove_abandoned_snapshot_paths(
            snapshot_paths
        )

        current_app.logger.exception(
            "Unable to queue Project %s",
            project.id,
        )

        raise ProjectExecutionQueueError(
            "Unable to queue the project."
        ) from exc

    return job
