"""Create a new queued Job from an existing Job's immutable snapshots."""

from dataclasses import dataclass

from flask import current_app

from app import db
from app.models import (
    Job,
    JobCredentialSnapshot,
    JobInventorySnapshot,
    JobPackageSnapshot,
    JobRepositorySnapshot,
    JobStep,
    JobStepExecutionSlice,
    Environment,
    RunnerEnvironment,
)
from app.services.job_inventory_snapshot import (
    JobInventorySnapshotError,
    delete_job_inventory_snapshot_path,
    read_job_inventory_snapshot_data,
    write_job_inventory_snapshot,
)
from app.services.runner_environments import (
    environment_revision,
    job_step_environment_requirement,
    runner_environment_state,
)
from app.services.project_concurrency import (
    launch_blocking_job,
    locked_project,
    normalise_concurrency_policy,
    parameter_signature_for_job,
    project_concurrency_message,
    scoped_parameter_signature,
)


TERMINAL_JOB_STATUSES = {"successful", "failed", "cancelled"}

RERUN_SCOPE_ALL = "all"
RERUN_SCOPE_FAILED = "failed"
RERUN_SCOPES = {RERUN_SCOPE_ALL, RERUN_SCOPE_FAILED}
FAILED_HOST_STATUSES = {"failed", "unreachable"}


class JobRerunError(ValueError):
    """An existing Job cannot safely be used as a rerun source."""


@dataclass(frozen=True)
class JobRerunResult:
    job: Job
    source_job: Job


def normalise_rerun_scope(value):
    scope = str(value or RERUN_SCOPE_ALL).strip().lower()
    if scope not in RERUN_SCOPES:
        raise JobRerunError(
            'Rerun scope must be "all" or "failed".'
        )
    return scope


def failed_hosts_for_rerun(source_job):
    """Return hosts whose final recorded result is failed/unreachable.

    Only hosts present in the immutable saved execution slices are returned.
    That makes the result safe to use as a rerun target and intentionally
    excludes historical/direct Jobs that have host-result rows but no saved
    per-host execution routing.
    """

    if source_job is None:
        return tuple()

    final_status_by_host = {}
    saved_slice_hosts = set()

    for step in sorted(source_job.steps, key=lambda item: item.position):
        for execution_slice in step.execution_slices:
            saved_slice_hosts.update(execution_slice.get_hosts())
        for result in step.host_results:
            host = str(result.host or "").strip()
            status = str(result.status or "").strip().lower()
            if host and status:
                final_status_by_host[host] = status

    return tuple(
        sorted(
            host
            for host, status in final_status_by_host.items()
            if status in FAILED_HOST_STATUSES and host in saved_slice_hosts
        )
    )


def _copy_package_snapshot(source):
    if source is None:
        return None
    return JobPackageSnapshot(
        package_id=source.package_id,
        package_name=source.package_name,
        package_owner=source.package_owner,
        package_definition_json=source.package_definition_json,
        package_definition_sha256=source.package_definition_sha256,
        display_values_json=source.display_values_json,
        operational_targets_json=source.operational_targets_json,
        inventory_bindings_json=source.inventory_bindings_json,
        encrypted_extra_vars=source.encrypted_extra_vars,
        extra_vars_format_version=source.extra_vars_format_version,
        step_limit=source.step_limit,
    )


def _short_revision(value):
    value = str(value or "").strip()
    return value[:12] if value else "not reported"


def rerun_preflight_issues(source_job):
    """Return reasons the saved execution snapshot cannot currently be rerun.

    Rerun preserves the original Environment revision.  Journeyman currently
    keeps one local copy of each Environment per execution node, so a saved
    revision that is no longer present on the exact snapshotted runner cannot
    be silently substituted with the current Environment.
    """

    if source_job is None or source_job.execution_type == "shell":
        return []

    issues = []
    for step in sorted(source_job.steps, key=lambda item: item.position):
        requirement = job_step_environment_requirement(step)
        if requirement is None:
            # Jobs created before Environment revision snapshots remain
            # rerunnable under their original compatibility behaviour.
            continue

        label = 'Step {} "{}"'.format(
            step.position,
            step.name or "Step {}".format(step.position),
        )
        slices = list(step.execution_slices)
        if not slices:
            issues.append(
                "{} has no saved execution slices.".format(label)
            )
            continue

        local_checked = False
        for execution_slice in slices:
            if execution_slice.dispatch_target == "local":
                if local_checked:
                    continue
                local_checked = True
                environment = db.session.get(Environment, requirement.environment_id)
                if environment is None:
                    issues.append(
                        '{} requires Environment "{}" revision {}, but that Environment '
                        "no longer exists.".format(
                            label, requirement.name, _short_revision(requirement.revision)
                        )
                    )
                    continue
                current_revision = environment_revision(environment)
                if current_revision != requirement.revision:
                    issues.append(
                        '{} requires Environment "{}" revision {}, but the built-in '
                        "runner now has revision {}. Journeyman does not retain the "
                        "historical local Environment build.".format(
                            label,
                            requirement.name,
                            _short_revision(requirement.revision),
                            _short_revision(current_revision),
                        )
                    )
                    continue
                if not environment.enabled or environment.validation_status != "passed":
                    issues.append(
                        '{} requires Environment "{}", but the current local Environment '
                        "is not enabled and validated.".format(label, requirement.name)
                    )
                continue

            if execution_slice.dispatch_target != "remote":
                continue

            runner = execution_slice.required_runner
            runner_name = execution_slice.runner_name or "unknown runner"
            if runner is None:
                issues.append(
                    '{} requires remote runner "{}", but that runner is no longer '
                    "available in Journeyman.".format(label, runner_name)
                )
                continue
            if not runner.enabled or not runner.is_registered:
                issues.append(
                    '{} requires remote runner "{}", but that runner is disabled or '
                    "unregistered.".format(label, runner.name)
                )
                continue

            required_capabilities = execution_slice.get_required_capabilities()
            missing_capabilities = sorted(
                required_capabilities.difference(runner.capabilities())
            )
            if missing_capabilities:
                issues.append(
                    '{} requires runner "{}" capabilities {}, which are no longer '
                    "available.".format(
                        label, runner.name, ", ".join(missing_capabilities)
                    )
                )
                continue

            state = runner_environment_state(runner, requirement)
            if state == "ready":
                continue

            row = RunnerEnvironment.query.filter_by(
                runner_id=runner.id,
                environment_id=requirement.environment_id,
            ).one_or_none()
            if state == "out_of_date":
                issues.append(
                    '{} requires Environment "{}" revision {} on runner "{}", but '
                    "that runner currently has revision {}. Launch the Project again "
                    "to use the current Environment.".format(
                        label,
                        requirement.name,
                        _short_revision(requirement.revision),
                        runner.name,
                        _short_revision(
                            row.environment_revision if row is not None else ""
                        ),
                    )
                )
            else:
                issues.append(
                    '{} requires Environment "{}" revision {} on runner "{}", but '
                    "that Environment is currently {}.".format(
                        label,
                        requirement.name,
                        _short_revision(requirement.revision),
                        runner.name,
                        state.replace("_", " "),
                    )
                )

    return issues


def _require_rerun_preflight(source_job):
    issues = rerun_preflight_issues(source_job)
    if not issues:
        return
    raise JobRerunError(
        "Job #{} cannot be rerun from its saved execution snapshot. {}".format(
            source_job.id,
            " ".join(issues),
        )
    )


def rerun_job(source_job, *, requested_by, source="Journeyman API", scope=RERUN_SCOPE_ALL):
    """Queue a new Job using the source Job's immutable execution snapshots.

    This deliberately does not resolve the current Project or Package
    definition. Repository revisions, credentials, inventory contents,
    Package execution values, workflow steps and slice routing are copied from
    the source Job so a rerun remains a rerun rather than a fresh dispatch.
    """

    requested_by = str(requested_by or "").strip()
    source = str(source or "Journeyman API").strip() or "Journeyman API"
    scope = normalise_rerun_scope(scope)
    if not requested_by:
        raise JobRerunError("No authenticated username was supplied to Journeyman.")
    if source_job is None:
        raise JobRerunError("Source Job was not found.")
    if source_job.status not in TERMINAL_JOB_STATUSES:
        raise JobRerunError(
            "Job #{} is {} and cannot be rerun until it has finished.".format(
                source_job.id, source_job.status
            )
        )
    if not source_job.steps:
        raise JobRerunError("Job #{} has no execution steps to rerun.".format(source_job.id))

    failed_hosts = set()
    if scope == RERUN_SCOPE_FAILED:
        failed_hosts = set(failed_hosts_for_rerun(source_job))
        if not failed_hosts:
            raise JobRerunError(
                "Job #{} has no saved failed or unreachable hosts that can be rerun."
                .format(source_job.id)
            )

    concurrency_policy = source_job.concurrency_policy or "unrestricted"
    concurrency_signature = parameter_signature_for_job(source_job)
    if scope == RERUN_SCOPE_FAILED:
        concurrency_signature = scoped_parameter_signature(
            concurrency_signature,
            scope=scope,
            hosts=failed_hosts,
        )
    if source_job.project is not None:
        project = locked_project(source_job.project)
        concurrency_policy = normalise_concurrency_policy(project.concurrency_policy)
        blocker = launch_blocking_job(
            project,
            concurrency_policy,
            concurrency_signature,
            exclude_job_id=source_job.id,
        )
        if blocker is not None:
            raise JobRerunError(
                project_concurrency_message(project, concurrency_policy, blocker)
            )

    _require_rerun_preflight(source_job)

    inventory_data = {}
    try:
        for snapshot in source_job.inventory_snapshots:
            inventory_data[snapshot.id] = read_job_inventory_snapshot_data(snapshot)
    except JobInventorySnapshotError as exc:
        raise JobRerunError(
            "Job #{} inventory snapshots cannot be verified for rerun.".format(source_job.id)
        ) from exc

    job = Job(
        project_id=source_job.project_id,
        project_name=source_job.project_name,
        status="queued",
        requested_by=requested_by,
        execution_type=source_job.execution_type,
        max_parallel_steps=source_job.max_parallel_steps,
        concurrency_policy=concurrency_policy,
        concurrency_signature=concurrency_signature,
        oversight_required_between_all_steps=source_job.oversight_required_between_all_steps,
        oversight_reviewer=requested_by,
        message=(
            "Rerun of Job #{} (failed hosts only) through {}.".format(source_job.id, source)
            if scope == RERUN_SCOPE_FAILED
            else "Rerun of Job #{} through {}.".format(source_job.id, source)
        ),
        dispatch_target=source_job.dispatch_target,
        required_runner_site=source_job.required_runner_site,
        required_runner_id=source_job.required_runner_id,
        default_runner_id=source_job.default_runner_id,
        default_runner_crew_id=source_job.default_runner_crew_id,
        required_runner_capabilities_json=source_job.required_runner_capabilities_json,
    )
    job.package_snapshot = _copy_package_snapshot(source_job.package_snapshot)

    repository_map = {}
    for source in source_job.repository_snapshots:
        target = JobRepositorySnapshot(
            repository_id=source.repository_id,
            repository_name=source.repository_name,
            repository_url=source.repository_url,
            repository_commit=source.repository_commit,
            repository_commit_message=source.repository_commit_message,
            repository_commit_author=source.repository_commit_author,
            repository_commit_at=source.repository_commit_at,
        )
        job.repository_snapshots.append(target)
        repository_map[source.id] = target

    credential_map = {}
    for source in source_job.credential_snapshots:
        target = JobCredentialSnapshot(
            credential_id=source.credential_id,
            credential_name=source.credential_name,
            credential_owner=source.credential_owner,
            credential_type=source.credential_type,
            username=source.username,
            encrypted_data=source.encrypted_data,
            secret_format_version=source.secret_format_version,
            credential_key_id=source.credential_key_id,
        )
        job.credential_snapshots.append(target)
        credential_map[source.id] = target

    inventory_map = {}
    for source in source_job.inventory_snapshots:
        target = JobInventorySnapshot(
            inventory_id=source.inventory_id,
            inventory_name=source.inventory_name,
            inventory_type=source.inventory_type,
            version=source.version,
            host_count=source.host_count,
        )
        job.inventory_snapshots.append(target)
        inventory_map[source.id] = target

    for source in sorted(source_job.steps, key=lambda item: item.position):
        selected_step_hosts = set()
        selected_slice_hosts = {}
        for source_slice in source.execution_slices:
            hosts = source_slice.get_hosts()
            if scope == RERUN_SCOPE_FAILED:
                hosts = [host for host in hosts if host in failed_hosts]
            selected_slice_hosts[source_slice.id] = hosts
            selected_step_hosts.update(hosts)

        target = JobStep(
            project_step_id=source.project_step_id,
            repository_snapshot=repository_map[source.job_repository_snapshot_id],
            inventory_snapshot=(
                inventory_map[source.job_inventory_snapshot_id]
                if source.job_inventory_snapshot_id is not None
                else None
            ),
            credential_snapshots=[credential_map[item.id] for item in source.credential_snapshots],
            position=source.position,
            name=source.name,
            environment_name=source.environment_name,
            environment_id=source.environment_id,
            environment_revision=source.environment_revision,
            environment_path=source.environment_path,
            ansible_config_path=source.ansible_config_path,
            playbook=source.playbook,
            limit=(
                ",".join(sorted(selected_step_hosts))
                if scope == RERUN_SCOPE_FAILED
                else source.limit
            ),
            tags=source.tags,
            skip_tags=source.skip_tags,
            extra_vars_json=source.extra_vars_json,
            verbosity=source.verbosity,
            check_mode=source.check_mode,
            remote_shell_become=source.remote_shell_become,
            remote_shell_serial=source.remote_shell_serial,
            continue_on_failure=source.continue_on_failure,
            failure_only=source.failure_only,
            refresh_inventory_after=source.refresh_inventory_after,
            depends_on_json=source.depends_on_json,
            oversight_required_before=source.oversight_required_before,
            oversight_approved=not source.oversight_required_before,
            status="pending",
        )
        for source_slice in sorted(source.execution_slices, key=lambda item: item.position):
            hosts = selected_slice_hosts[source_slice.id]
            target_slice = JobStepExecutionSlice(
                position=source_slice.position,
                dispatch_target=source_slice.dispatch_target,
                required_runner_id=source_slice.required_runner_id,
                runner_name=source_slice.runner_name,
                runner_hostname=source_slice.runner_hostname,
                required_runner_capabilities_json=source_slice.required_runner_capabilities_json,
                status="pending" if hosts else "successful",
                exit_code=None if hosts else 0,
                message=(
                    ""
                    if hosts
                    else "No failed hosts from the source Job target this execution slice."
                ),
            )
            target_slice.set_hosts(hosts)
            target.execution_slices.append(target_slice)
        job.steps.append(target)

    db.session.add(job)
    snapshot_paths = []
    try:
        db.session.flush()
        for source_id, target in inventory_map.items():
            snapshot_paths.append(
                write_job_inventory_snapshot(target, inventory_data[source_id])
            )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        for path in reversed(snapshot_paths):
            try:
                delete_job_inventory_snapshot_path(path)
            except JobInventorySnapshotError:
                current_app.logger.exception(
                    "Unable to remove abandoned rerun inventory snapshot %s", path
                )
        current_app.logger.exception("Unable to rerun Job %s", source_job.id)
        raise JobRerunError("Unable to queue the Job rerun.") from exc

    return JobRerunResult(job=job, source_job=source_job)
