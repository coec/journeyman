"""Explicit synchronization of execution Environments to remote runners."""

from datetime import datetime, timezone
from pathlib import Path
import re

from app import db
from app.models import Environment, RunnerEnvironment, RunnerEnvironmentSync
from app.services.environment_build_settings import build_proxy_environment
from app.services.environments import (
    APPLICATION_ENVIRONMENT_NAME,
    SYSTEM_ENVIRONMENT_PATH,
)
from app.services.runner_environments import (
    ensure_runner_environment_row,
    environment_revision,
)
from app.services.runners import CURRENT_REMOTE_RUNNER_VERSION, runner_update_available
_SYNC_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
)


class RunnerEnvironmentSyncError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def is_syncable_environment(environment):
    if not environment or not environment.enabled:
        return False
    if environment.name == APPLICATION_ENVIRONMENT_NAME:
        return False
    if environment.path == SYSTEM_ENVIRONMENT_PATH or environment.is_builtin:
        return False
    if environment.validation_status != "passed":
        return False
    # Journeyman-managed Environments must have a successful managed build.
    # Registered/external Environments are already built outside Journeyman;
    # successful validation is sufficient to use their declared portable spec.
    if environment.is_managed and environment.build_status != "passed":
        return False
    return True


def _ansible_core_release_series(version_output):
    match = re.match(
        r"^ansible-playbook\s+\[core\s+(\d+)\.(\d+)(?:[.\]\s]|$)",
        str(version_output or "").strip(),
    )
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _ansible_compatibility_requirement(environment):
    """Constrain remote ansible-core to the central Environment's major.minor series."""
    series = _ansible_core_release_series(environment.ansible_version)
    if series is None:
        return str(environment.ansible_spec or "ansible-core")
    major, minor = series
    return "ansible-core>={}.{},<{}.{}".format(major, minor, major, minor + 1)


def validate_syncable_environment(environment):
    if environment is None:
        raise RunnerEnvironmentSyncError("Environment was not found.")
    if environment.name == APPLICATION_ENVIRONMENT_NAME:
        raise RunnerEnvironmentSyncError(
            "The Journeyman application environment is not an execution environment."
        )
    if environment.path == SYSTEM_ENVIRONMENT_PATH or environment.is_builtin:
        raise RunnerEnvironmentSyncError(
            "Built-in environments are intrinsic to each runner and cannot be synchronized."
        )
    if not environment.enabled:
        raise RunnerEnvironmentSyncError("Disabled environments cannot be synchronized.")
    if environment.validation_status != "passed":
        raise RunnerEnvironmentSyncError(
            "The environment must be validated successfully before synchronization."
        )
    if environment.is_managed and environment.build_status != "passed":
        raise RunnerEnvironmentSyncError(
            "The Journeyman-managed environment must be built successfully before synchronization."
        )


def queue_environment_sync(environment, runner):
    validate_syncable_environment(environment)
    if runner is None or runner.is_local:
        raise RunnerEnvironmentSyncError("Environment synchronization requires a remote runner.")
    if not runner.is_registered:
        raise RunnerEnvironmentSyncError(
            'Runner "{}" is not registered.'.format(runner.name)
        )
    if runner_update_available(runner):
        raise RunnerEnvironmentSyncError(
            'Runner "{}" must be updated to {} before Environment synchronization.'.format(
                runner.name, CURRENT_REMOTE_RUNNER_VERSION
            )
        )

    revision = environment_revision(environment)
    row = RunnerEnvironmentSync.query.filter_by(
        runner_id=runner.id,
        environment_id=environment.id,
    ).one_or_none()
    if row is None:
        row = RunnerEnvironmentSync(runner=runner, environment=environment)
        db.session.add(row)

    row.requested_revision = revision
    row.status = "queued"
    row.message = "Waiting for runner to claim Environment synchronization."
    row.requested_at = _now()
    row.started_at = None
    row.completed_at = None
    db.session.flush()
    return row


def claim_next_environment_sync(runner):
    """Atomically claim one queued Environment synchronization for ``runner``."""

    from app.services.runners import runner_health
    if runner_health(runner) != "healthy":
        return None

    while True:
        candidate = (
            RunnerEnvironmentSync.query
            .filter_by(runner_id=runner.id, status="queued")
            .order_by(
                RunnerEnvironmentSync.requested_at.asc(),
                RunnerEnvironmentSync.id.asc(),
            )
            .first()
        )
        if candidate is None:
            return None

        environment = candidate.environment
        try:
            validate_syncable_environment(environment)
        except RunnerEnvironmentSyncError as exc:
            candidate.status = "failed"
            candidate.message = str(exc)
            candidate.completed_at = _now()
            db.session.commit()
            continue

        current_revision = environment_revision(environment)
        if current_revision != candidate.requested_revision:
            candidate.status = "failed"
            candidate.message = (
                "Environment definition changed after synchronization was queued; "
                "queue synchronization again."
            )
            candidate.completed_at = _now()
            db.session.commit()
            continue

        started_at = _now()
        updated = (
            RunnerEnvironmentSync.query
            .filter(
                RunnerEnvironmentSync.id == candidate.id,
                RunnerEnvironmentSync.status == "queued",
            )
            .update(
                {
                    RunnerEnvironmentSync.status: "building",
                    RunnerEnvironmentSync.message: "Runner is building the Environment.",
                    RunnerEnvironmentSync.started_at: started_at,
                    RunnerEnvironmentSync.completed_at: None,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.session.rollback()
            continue
        db.session.commit()
        return db.session.get(RunnerEnvironmentSync, candidate.id)


def _portable_python_command(environment):
    version = str(environment.python_version or "").strip()
    match = re.search(r"\bPython\s+(\d+)\.(\d+)", version)
    if match:
        return "python{}.{}".format(match.group(1), match.group(2))
    interpreter = Path(str(environment.python_interpreter or "python3")).name
    return interpreter or "python3"


def environment_sync_manifest(sync):
    environment = sync.environment
    proxy_environment = build_proxy_environment(
        {}, proxy_credential=environment.proxy_credential
    )
    ansible_config_content = ""
    config_path = Path(
        str(environment.ansible_config_path or "/etc/ansible/ansible.cfg")
    )
    if config_path.is_file():
        if config_path.stat().st_size > 1024 * 1024:
            raise RunnerEnvironmentSyncError(
                "Ansible configuration is too large to synchronize."
            )
        try:
            ansible_config_content = config_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RunnerEnvironmentSyncError(
                "Unable to read Ansible configuration for synchronization."
            ) from exc
    return {
        "sync_id": sync.id,
        "environment": {
            "environment_id": environment.id,
            "name": environment.name,
            "revision": sync.requested_revision,
            "python_command": _portable_python_command(environment),
            "ansible_spec": environment.ansible_spec or "ansible-core",
            "ansible_compatibility_requirement": _ansible_compatibility_requirement(environment),
            "expected_python_version": str(environment.python_version or ""),
            "expected_ansible_version": str(environment.ansible_version or ""),
            "pip_requirements": [
                line.strip()
                for line in str(environment.pip_requirements or "").splitlines()
                if line.strip()
            ],
            "system_requirements": [
                line.strip()
                for line in str(environment.system_requirements or "").splitlines()
                if line.strip()
            ],
            "collection_requirements": [
                line.strip()
                for line in str(environment.collection_requirements or "").splitlines()
                if line.strip()
            ],
            "ansible_config": ansible_config_content,
            "proxy_environment": {
                key: str(proxy_environment[key])
                for key in _SYNC_PROXY_KEYS
                if key in proxy_environment and str(proxy_environment[key])
            },
        },
    }


def complete_environment_sync(sync, runner, payload):
    if sync.runner_id != runner.id:
        raise RunnerEnvironmentSyncError("Environment synchronization is assigned to another runner.")
    if sync.status != "building":
        raise RunnerEnvironmentSyncError("Environment synchronization is not currently building.")

    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "failed"}:
        raise RunnerEnvironmentSyncError("Environment synchronization status must be ready or failed.")

    revision = str(payload.get("revision") or "").strip()[:64]
    local_path = str(payload.get("path") or "").strip()[:1000]
    message = str(payload.get("message") or "").strip()[:12000]
    now = _now()

    if status == "ready":
        if revision != sync.requested_revision:
            raise RunnerEnvironmentSyncError(
                "Runner completed synchronization with an unexpected Environment revision."
            )
        if not local_path:
            raise RunnerEnvironmentSyncError(
                "Runner completed synchronization without an Environment path."
            )
        state = ensure_runner_environment_row(runner, sync.environment)
        state.status = "ready"
        state.environment_revision = revision
        state.local_path = local_path
        state.message = message or "Environment synchronized successfully."
        state.last_reported_at = now
        sync.status = "successful"
        sync.message = state.message
    else:
        sync.status = "failed"
        sync.message = message or "Runner Environment synchronization failed."

    sync.completed_at = now
    db.session.commit()
    return sync


def environment_sync_rows(environment, runners):
    syncs = {
        row.runner_id: row
        for row in RunnerEnvironmentSync.query.filter_by(
            environment_id=environment.id
        ).all()
    }
    states = {
        row.runner_id: row
        for row in RunnerEnvironment.query.filter_by(
            environment_id=environment.id
        ).all()
    }
    expected_revision = environment_revision(environment)
    result = []
    for runner in runners:
        state = states.get(runner.id)
        sync = syncs.get(runner.id)
        if state is None:
            installed_state = "not_installed"
        elif state.status == "ready" and state.environment_revision == expected_revision:
            installed_state = "ready"
        elif state.status == "ready":
            installed_state = "out_of_date"
        elif state.status in {"building", "failed"}:
            installed_state = state.status
        else:
            installed_state = "not_installed"
        result.append(
            {
                "runner": runner,
                "state": installed_state,
                "sync": sync,
                "sync_supported": not runner_update_available(runner),
                "required_runner_version": CURRENT_REMOTE_RUNNER_VERSION,
            }
        )
    return result
