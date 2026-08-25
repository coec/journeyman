"""
Safe pre-run Project target preview.

Only target hostnames and execution metadata are exposed to templates.
Resolved host variables remain server-side and are passed directly to
the immutable Job queue service after confirmation.
"""

import hashlib
import json
from dataclasses import dataclass

from flask import current_app

from .inventory_resolver import (
    InventoryResolutionError,
    refresh_inventory,
    resolve_inventory,
)
from .execution_target_hosts import (
    ExecutionTargetResolutionError,
    target_hosts_for_inventory,
)
from .project_repositories import (
    ProjectRepositoryRefreshError,
    refresh_project_repositories,
)


class ProjectExecutionPreviewError(Exception):
    """
    The Project cannot be previewed safely.

    The exception message may be displayed in the web interface.
    """


@dataclass(frozen=True)
class ProjectExecutionPreviewStep:
    project_step_id: int
    position: int
    name: str
    playbook: str
    repository_name: str
    repository_commit: str
    inventory_id: int
    inventory_name: str
    inventory_type: str
    inventory_is_override: bool
    refresh_inventory_after: bool
    refresh_affects_filtered_targets: bool
    target_hosts_may_change: bool
    limit: str
    target_hosts: tuple

    @property
    def target_count(self):
        return len(
            self.target_hosts
        )


@dataclass
class ProjectExecutionPreview:
    project_id: int
    project_name: str
    steps: tuple
    total_unique_hosts: int
    digest: str
    large_target_threshold: int
    resolved_inventory_data: dict

    @property
    def has_large_target(self):
        return any(
            step.target_count
            >= self.large_target_threshold
            for step in self.steps
        )

    @property
    def has_zero_target(self):
        return any(
            step.target_count == 0
            for step in self.steps
        )

    @property
    def has_dynamic_targets(self):
        return any(
            step.target_hosts_may_change
            for step in self.steps
        )


def _dependency_ancestor_positions(step, steps_by_position):
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


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_json(value):
    return hashlib.sha256(
        _canonical_json_bytes(value)
    ).hexdigest()


def _repository_commit(repository):
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

    return ""


def _credential_fingerprint(credential):
    encrypted_data = (
        credential.encrypted_data
        or b""
    )

    if isinstance(
        encrypted_data,
        str,
    ):
        encrypted_data = (
            encrypted_data.encode(
                "utf-8"
            )
        )

    return {
        "id": credential.id,
        "type": credential.credential_type,
        "owner": credential.owner,
        "username": credential.username or "",
        "format_version": (
            credential.secret_format_version
        ),
        "encrypted_sha256": (
            hashlib.sha256(
                encrypted_data
            ).hexdigest()
        ),
    }


def build_project_execution_preview(
    project,
    step_limit_override=None,
    refresh_repositories=False,
    refresh_inventory_sources=False,
    inventory_bindings=None,
    progress=None,
):
    """
    Resolve and preview the Project's current enabled workflow.

    Returns a ProjectExecutionPreview containing safe template data and
    the resolved inventories needed to queue exactly what was reviewed.
    """

    if progress is not None:
        progress("validate", "Validating Project configuration")

    if refresh_repositories:
        if progress is not None:
            progress("repository", "Synchronizing Project repositories")
        try:
            refresh_project_repositories(project)
        except ProjectRepositoryRefreshError as exc:
            raise ProjectExecutionPreviewError(str(exc)) from exc

    if not project.enabled:
        raise ProjectExecutionPreviewError(
            "This project is disabled and cannot be run."
        )

    project_steps = sorted(
        (
            step
            for step in project.steps
            if step.enabled
        ),
        key=lambda step: step.position,
    )

    if not project_steps:
        raise ProjectExecutionPreviewError(
            "This project has no enabled workflow steps."
        )

    effective_inventories = {}
    step_inventory_ids = {}

    for position, step in enumerate(
        project_steps,
        start=1,
    ):
        step_name = (
            step.name
            or "Step {}".format(position)
        )

        repository = step.effective_repository()

        if repository is None:
            raise ProjectExecutionPreviewError(
                'Step "{}" has no repository.'
                .format(step_name)
            )

        if repository.status != "up_to_date":
            raise ProjectExecutionPreviewError(
                'Repository "{}" must be synchronized.'
                .format(repository.name)
            )

        repository_commit = (
            _repository_commit(
                repository
            )
        )

        if not repository_commit:
            raise ProjectExecutionPreviewError(
                'Repository "{}" has no recorded synchronized '
                "commit."
                .format(repository.name)
            )

        inventory = (
            step.inventory
            or project.inventory
        )

        if inventory is None:
            raise ProjectExecutionPreviewError(
                'Step {} "{}" has no effective inventory.'
                .format(
                    position,
                    step_name,
                )
            )

        if not inventory.enabled:
            raise ProjectExecutionPreviewError(
                'Step {} inventory "{}" is disabled.'
                .format(
                    position,
                    inventory.name,
                )
            )

        for credential in step.effective_credentials():
            if credential.encrypted_data is None:
                raise ProjectExecutionPreviewError(
                    'Credential "{}" has no stored secret data.'
                    .format(
                        credential.name
                    )
                )

        step_inventory_ids[
            step.id
        ] = inventory.id

        effective_inventories.setdefault(
            inventory.id,
            inventory,
        )

    steps_by_position = {
        step.position: step
        for step in project_steps
    }
    dependency_ancestors = {
        step.id: _dependency_ancestor_positions(
            step,
            steps_by_position,
        )
        for step in project_steps
    }

    target_hosts_may_change = {}
    refresh_affects_filtered_targets = {}

    for step in project_steps:
        inventory = effective_inventories[
            step_inventory_ids[step.id]
        ]
        target_hosts_may_change[step.id] = (
            inventory.inventory_type == "filtered"
            and any(
                steps_by_position[ancestor_position]
                .refresh_inventory_after
                for ancestor_position
                in dependency_ancestors[step.id]
                if ancestor_position in steps_by_position
            )
        )

        refresh_affects_filtered_targets[step.id] = (
            bool(step.refresh_inventory_after)
            and any(
                step.position
                in dependency_ancestors[candidate.id]
                and effective_inventories[
                    step_inventory_ids[candidate.id]
                ].inventory_type == "filtered"
                for candidate in project_steps
                if candidate.id != step.id
            )
        )

    resolved_inventory_data = {}
    refreshed_inventory_data = {}

    for inventory_id, inventory in (
        effective_inventories.items()
    ):
        if progress is not None:
            progress(
                "inventory",
                'Resolving inventory "{}"'.format(inventory.name),
            )
        try:
            if refresh_inventory_sources:
                resolved_inventory_data[
                    inventory_id
                ] = refresh_inventory(
                    inventory,
                    refreshed_inventory_data=(
                        refreshed_inventory_data
                    ),
                    bindings=inventory_bindings,
                )
            else:
                resolved_inventory_data[
                    inventory_id
                ] = resolve_inventory(
                    inventory,
                    bindings=inventory_bindings,
                )

        except InventoryResolutionError as exc:
            current_app.logger.warning(
                "Unable to preview Inventory %s "
                "for Project %s: %s",
                inventory.id,
                project.id,
                exc,
            )

            raise ProjectExecutionPreviewError(
                'Unable to resolve inventory "{}": {}'
                .format(
                    inventory.name,
                    exc,
                )
            ) from exc

    preview_steps = []
    all_target_hosts = set()
    digest_steps = []

    inventory_hashes = {
        str(inventory_id): _sha256_json(
            inventory_data
        )
        for inventory_id, inventory_data
        in resolved_inventory_data.items()
    }

    for position, step in enumerate(
        project_steps,
        start=1,
    ):
        inventory_id = (
            step_inventory_ids[
                step.id
            ]
        )

        inventory = (
            effective_inventories[
                inventory_id
            ]
        )

        repository = step.effective_repository()

        repository_commit = (
            _repository_commit(
                repository
            )
        )

        if step_limit_override is None:
            limit = str(
                step.limit or ""
            ).strip()
        else:
            limit = str(
                step_limit_override or ""
            ).strip()

        try:
            target_hosts = target_hosts_for_inventory(
                resolved_inventory_data[inventory_id],
                limit,
            )
        except ExecutionTargetResolutionError as exc:
            raise ProjectExecutionPreviewError(str(exc)) from exc

        all_target_hosts.update(
            target_hosts
        )

        if progress is not None:
            progress(
                "targets",
                'Resolved Step {} "{}"'.format(position, step.name or "Step {}".format(position)),
                "{} host{}".format(len(target_hosts), "" if len(target_hosts) == 1 else "s"),
            )

        preview_step = (
            ProjectExecutionPreviewStep(
                project_step_id=step.id,
                position=position,
                name=(
                    step.name
                    or "Step {}".format(position)
                ),
                playbook=step.playbook,
                repository_name=(
                    repository.name
                ),
                repository_commit=(
                    repository_commit
                ),
                inventory_id=inventory.id,
                inventory_name=inventory.name,
                inventory_type=inventory.inventory_type,
                inventory_is_override=(
                    step.inventory_id
                    is not None
                ),
                refresh_inventory_after=bool(
                    step.refresh_inventory_after
                ),
                refresh_affects_filtered_targets=(
                    refresh_affects_filtered_targets[step.id]
                ),
                target_hosts_may_change=(
                    target_hosts_may_change[step.id]
                ),
                limit=limit,
                target_hosts=target_hosts,
            )
        )

        preview_steps.append(
            preview_step
        )

        digest_steps.append(
            {
                "project_step_id": step.id,
                "position": position,
                "name": preview_step.name,
                "playbook": step.playbook,
                "repository_id": repository.id,
                "repository_commit": (
                    repository_commit
                ),
                "inventory_id": inventory.id,
                "inventory_sha256": (
                    inventory_hashes[
                        str(inventory.id)
                    ]
                ),
                "limit": limit,
                "target_hosts": list(
                    target_hosts
                ),
                "tags": step.tags or "",
                "skip_tags": (
                    step.skip_tags or ""
                ),
                "verbosity": step.verbosity,
                "check_mode": bool(step.check_mode),
                "continue_on_failure": (
                    step.continue_on_failure
                ),
                "credentials": [
                    _credential_fingerprint(
                        credential
                    )
                    for credential
                    in sorted(
                        step.effective_credentials(),
                        key=lambda value: value.id,
                    )
                ],
            }
        )

    digest_payload = {
        "project_id": project.id,
        "project_name": project.name,
        "execution_type": project.execution_type or "ansible",
        "steps": digest_steps,
    }

    threshold = int(
        current_app.config.get(
            "PROJECT_RUN_LARGE_TARGET_THRESHOLD",
            25,
        )
    )

    threshold = max(
        threshold,
        1,
    )

    if progress is not None:
        progress("review", "Preparing dispatch review")

    return ProjectExecutionPreview(
        project_id=project.id,
        project_name=project.name,
        steps=tuple(
            preview_steps
        ),
        total_unique_hosts=len(
            all_target_hosts
        ),
        digest=_sha256_json(
            digest_payload
        ),
        large_target_threshold=threshold,
        resolved_inventory_data=(
            resolved_inventory_data
        ),
    )
