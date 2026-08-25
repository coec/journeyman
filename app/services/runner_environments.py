"""Runner-local execution-environment reporting and eligibility checks."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Environment, RunnerEnvironment
from app.services.environments import (
    APPLICATION_ENVIRONMENT_NAME,
    SYSTEM_ENVIRONMENT_PATH,
)


SYSTEM_ENVIRONMENT_REVISION = "builtin-system-v1"
REPORTED_ENVIRONMENT_STATES = {"ready", "building", "failed"}


class RunnerEnvironmentUnavailable(RuntimeError):
    """A runner does not have the required execution environment ready."""


@dataclass(frozen=True)
class EnvironmentRequirement:
    environment_id: int
    name: str
    revision: str


def _normalised_lines(value):
    return [
        str(item).strip()
        for item in str(value or "").splitlines()
        if str(item).strip()
    ]


def _ansible_release_series(version_output):
    value = str(version_output or "").strip()
    marker = "ansible-playbook [core "
    if not value.startswith(marker):
        return value
    remainder = value[len(marker):]
    version = remainder.split("]", 1)[0].strip()
    parts = version.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return value
    return "{}.{}".format(parts[0], parts[1])


def environment_revision(environment):
    """Return a portable revision for an Environment definition/build.

    Node-local absolute virtualenv paths and proxy credentials are intentionally
    excluded.  A runner may install the same Environment at a different local
    path without changing its portable identity.
    """

    if environment.path == SYSTEM_ENVIRONMENT_PATH:
        return SYSTEM_ENVIRONMENT_REVISION

    ansible_config_path = str(
        environment.ansible_config_path or "/etc/ansible/ansible.cfg"
    )
    ansible_config_sha256 = ""
    try:
        with open(ansible_config_path, "rb") as config_file:
            config_bytes = config_file.read(1024 * 1024 + 1)
        if len(config_bytes) <= 1024 * 1024:
            ansible_config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    except OSError:
        pass

    payload = {
        "schema": 3,
        "name": str(environment.name or ""),
        "is_managed": bool(environment.is_managed),
        "ansible_spec": str(environment.ansible_spec or "ansible-core"),
        "pip_requirements": _normalised_lines(environment.pip_requirements),
        "collection_requirements": _normalised_lines(
            environment.collection_requirements
        ),
        # Python remains exact, but ansible-core patch releases within the same
        # major.minor series are execution-compatible for runner eligibility.
        "python_version": str(environment.python_version or ""),
        "ansible_release_series": _ansible_release_series(environment.ansible_version),
        "ansible_config_path": ansible_config_path,
        "ansible_config_sha256": ansible_config_sha256,
    }
    system_requirements = _normalised_lines(
        getattr(environment, "system_requirements", "")
    )
    if system_requirements:
        payload["system_requirements"] = system_requirements

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def environment_requirement(environment):
    if environment is None or environment.id is None:
        return None
    return EnvironmentRequirement(
        environment_id=int(environment.id),
        name=str(environment.name or ""),
        revision=environment_revision(environment),
    )


def job_step_environment_requirement(step):
    environment_id = getattr(step, "environment_id", None)
    revision = str(getattr(step, "environment_revision", "") or "").strip()
    if environment_id is None or not revision:
        # Jobs queued before runner-environment validation was introduced do not
        # gain a new dispatch requirement retrospectively.
        return None
    return EnvironmentRequirement(
        environment_id=int(environment_id),
        name=str(getattr(step, "environment_name", "") or ""),
        revision=revision,
    )


def _reported_row(runner, requirement):
    if runner is None or requirement is None:
        return None
    return RunnerEnvironment.query.filter_by(
        runner_id=runner.id,
        environment_id=requirement.environment_id,
    ).one_or_none()


def runner_environment_state(runner, requirement):
    """Return ready/building/failed/out_of_date/not_installed for a runner."""

    if requirement is None:
        return "ready"

    row = _reported_row(runner, requirement)
    if row is None:
        return "not_installed"

    status = str(row.status or "").strip().lower()
    if status == "ready" and row.environment_revision != requirement.revision:
        return "out_of_date"
    if status in REPORTED_ENVIRONMENT_STATES:
        return status
    return "not_installed"


def runner_environment_ready(runner, requirement):
    return runner_environment_state(runner, requirement) == "ready"


def runner_environment_local_path(runner, requirement):
    state = runner_environment_state(runner, requirement)
    row = _reported_row(runner, requirement)
    if state != "ready" or row is None or not str(row.local_path or "").strip():
        raise RunnerEnvironmentUnavailable(
            'Runner "{}" does not have execution environment "{}" ready ({}).'.format(
                getattr(runner, "name", ""),
                requirement.name,
                state.replace("_", " "),
            )
        )
    return str(row.local_path).strip()


def require_runner_environment(runner, requirement):
    if requirement is None:
        return
    state = runner_environment_state(runner, requirement)
    if state != "ready":
        raise RunnerEnvironmentUnavailable(
            'Runner "{}" does not have execution environment "{}" ready ({}).'.format(
                getattr(runner, "name", ""),
                requirement.name,
                state.replace("_", " "),
            )
        )


def ensure_runner_environment_row(runner, environment):
    """Return the unique runner/environment state row, creating it race-safely.

    Runner heartbeats and explicit Environment synchronization completion can
    report the same newly-installed Environment concurrently.  Both writers may
    therefore observe no row before one of them inserts it.  Keep the database
    uniqueness constraint as the arbiter and isolate the speculative INSERT in
    a SAVEPOINT so a duplicate race does not poison the caller's transaction.
    """

    filters = {
        "runner_id": runner.id,
        "environment_id": environment.id,
    }
    row = RunnerEnvironment.query.filter_by(**filters).one_or_none()
    if row is not None:
        return row

    try:
        with db.session.begin_nested():
            row = RunnerEnvironment(
                runner_id=runner.id,
                environment_id=environment.id,
            )
            db.session.add(row)
            db.session.flush()
    except IntegrityError:
        # A concurrent heartbeat/completion inserted the same unique pair.
        # begin_nested() has rolled back only the SAVEPOINT, leaving the outer
        # request transaction usable.  Re-read and update the winning row.
        row = RunnerEnvironment.query.filter_by(**filters).one_or_none()
        if row is None:
            raise

    return row


def _resolve_reported_environment(item):
    environment = None
    raw_id = item.get("environment_id")
    if raw_id not in (None, ""):
        try:
            environment = db.session.get(Environment, int(raw_id))
        except (TypeError, ValueError):
            raise ValueError("environment_id must be an integer.")

    if environment is None:
        name = str(item.get("name") or "").strip()
        if name:
            environment = Environment.query.filter_by(name=name).one_or_none()
    return environment


def set_reported_runner_environments(runner, payload):
    """Replace a runner's reported installed Environment set from heartbeat."""

    if not isinstance(payload, list):
        raise ValueError("environments must be a list.")

    now = datetime.now(timezone.utc)
    seen_environment_ids = set()

    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each environments item must be an object.")

        environment = _resolve_reported_environment(item)
        if environment is None:
            # A stale manifest for an Environment removed from the server must
            # not make the entire runner heartbeat fail.
            continue

        status = str(item.get("status") or "").strip().lower()
        if status not in REPORTED_ENVIRONMENT_STATES:
            raise ValueError(
                "Environment status must be ready, building, or failed."
            )

        revision = str(item.get("revision") or "").strip()[:64]
        local_path = str(item.get("path") or "").strip()[:1000]
        message = str(item.get("message") or "").strip()[:2000]

        if status == "ready" and (not revision or not local_path):
            status = "failed"
            if not message:
                message = "Runner reported an incomplete Environment manifest."

        row = ensure_runner_environment_row(runner, environment)

        row.status = status
        row.environment_revision = revision
        row.local_path = local_path
        row.message = message
        row.last_reported_at = now
        seen_environment_ids.add(environment.id)

    for row in list(runner.environment_states):
        if row.environment_id not in seen_environment_ids:
            db.session.delete(row)


def runner_environment_rows(runner):
    """Return UI rows for every enabled Environment on this runner."""

    environments = (
        Environment.query
        .filter(
            Environment.enabled.is_(True),
            Environment.name != APPLICATION_ENVIRONMENT_NAME,
        )
        .order_by(Environment.name.asc())
        .all()
    )
    reported = {
        row.environment_id: row
        for row in getattr(runner, "environment_states", [])
    }
    syncs = {
        row.environment_id: row
        for row in getattr(runner, "environment_syncs", [])
    }
    rows = []
    for environment in environments:
        requirement = environment_requirement(environment)
        row = reported.get(environment.id)
        sync = syncs.get(environment.id)

        if runner.is_local:
            state = "ready" if environment.validation_status == "passed" else "failed"
            local_path = environment.path
            message = environment.validation_message or ""
        else:
            state = runner_environment_state(runner, requirement)
            local_path = row.local_path if row is not None else ""
            message = row.message if row is not None else ""
            if sync is not None and sync.status == "queued":
                state = "queued"
                message = sync.message or "Waiting for runner to claim Environment synchronization."
            elif sync is not None and sync.status == "building":
                state = "building"
                message = sync.message or "Runner is building the Environment."
            elif sync is not None and sync.status == "failed" and state != "ready":
                message = sync.message or "The last Environment synchronization failed."
                if state == "not_installed":
                    state = "failed"
            elif state == "out_of_date":
                message = "Runner Environment revision is out of date."
            elif state == "not_installed" and not message:
                message = "Not installed on this runner."

        rows.append(
            {
                "environment": environment,
                "state": state,
                "local_path": local_path,
                "message": message,
                "expected_revision": requirement.revision,
                "reported_revision": (
                    row.environment_revision if row is not None else ""
                ),
            }
        )
    return rows
