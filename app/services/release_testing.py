import re

from app import db
from app.credential_types import CREDENTIAL_TYPE_MACHINE
from app.models import Credential, Inventory, ReleaseTestSetting, RunnerCrew


class ReleaseTestSettingsError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(" ".join(self.errors))


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@^+-]+$")


def get_or_create_release_test_settings():
    settings = db.session.get(ReleaseTestSetting, 1)
    if settings is None:
        settings = ReleaseTestSetting(id=1)
        db.session.add(settings)
        db.session.commit()
    return settings


def settings_to_form_data(settings):
    return {
        "inventory_id": settings.inventory_id,
        "credential_id": settings.credential_id,
        "runner_crew_id": settings.runner_crew_id,
        "host_pattern": settings.host_pattern or "",
        "alternate_become_users": settings.alternate_become_users or "",
    }


def form_data(form):
    def integer_or_none(name):
        raw = str(form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return -1

    return {
        "inventory_id": integer_or_none("inventory_id"),
        "credential_id": integer_or_none("credential_id"),
        "runner_crew_id": integer_or_none("runner_crew_id"),
        "host_pattern": str(form.get("host_pattern") or "").strip(),
        "alternate_become_users": str(form.get("alternate_become_users") or "").strip(),
    }


def validate(values):
    errors = []
    inventory = db.session.get(Inventory, values["inventory_id"]) if values["inventory_id"] else None
    credential = db.session.get(Credential, values["credential_id"]) if values["credential_id"] else None
    crew = db.session.get(RunnerCrew, values["runner_crew_id"]) if values["runner_crew_id"] else None

    if inventory is None or not inventory.enabled:
        errors.append("Select an enabled test inventory.")
    if credential is None or credential.credential_type != CREDENTIAL_TYPE_MACHINE:
        errors.append("Select a Linux/UNIX Machine credential.")
    if not values["host_pattern"]:
        errors.append("A test host or Ansible host pattern is required.")
    elif len(values["host_pattern"]) > 500:
        errors.append("Test host pattern is too long.")
    if values["runner_crew_id"] is not None:
        if crew is None or not crew.enabled:
            errors.append("Select an enabled Runner Crew.")
        elif not crew.runners:
            errors.append("The selected Runner Crew has no members.")

    become_users = []
    for raw in values["alternate_become_users"].splitlines():
        username = raw.strip()
        if not username:
            continue
        if len(username) > 128 or not _USERNAME_RE.fullmatch(username):
            errors.append("Alternate become user {!r} is invalid.".format(username))
            continue
        if username not in become_users:
            become_users.append(username)

    if errors:
        raise ReleaseTestSettingsError(errors)

    result = dict(values)
    result["alternate_become_users"] = "\n".join(become_users)
    result["inventory"] = inventory
    result["credential"] = credential
    result["runner_crew"] = crew
    return result


def update(settings, values, username):
    settings.inventory_id = values["inventory"].id
    settings.credential_id = values["credential"].id
    settings.runner_crew_id = values["runner_crew"].id if values["runner_crew"] else None
    settings.host_pattern = values["host_pattern"]
    settings.alternate_become_users = values["alternate_become_users"]
    settings.updated_by = username
    db.session.commit()
    return settings


def is_configured(settings):
    return bool(settings.inventory_id and settings.credential_id and settings.host_pattern)

EXPECTED_PARTIAL_FAILURE_MARKER = "JOURNEYMAN_EXPECTED_PARTIAL_FAILURE"
_FINAL_JOB_STATUSES = {"successful", "failed", "cancelled"}


def evaluate_validation_job(job, *, expected_failure=False):
    """Evaluate one completed release-validation Job without changing its state."""
    if job is None:
        return {"state": "not_run", "passed": None, "message": "Not run yet."}

    if job.status not in _FINAL_JOB_STATUSES:
        return {
            "state": "running",
            "passed": None,
            "job": job,
            "message": "Job #{} is {}.".format(job.id, job.status),
        }

    steps = list(getattr(job, "steps", []) or [])
    slices = [item for step in steps for item in (getattr(step, "execution_slices", []) or [])]
    max_slice_hosts = max(
        [int(getattr(item, "host_count", 0) or 0) for item in slices] or [0]
    )

    if expected_failure:
        marker_present = any(
            EXPECTED_PARTIAL_FAILURE_MARKER in str(getattr(step, "stdout", "") or "")
            or any(
                EXPECTED_PARTIAL_FAILURE_MARKER
                in str(getattr(item, "stdout", "") or "")
                for item in (getattr(step, "execution_slices", []) or [])
            )
            for step in steps
        )
        step_failed = any(getattr(step, "status", "") == "failed" for step in steps)
        slice_failed = any(getattr(item, "status", "") == "failed" for item in slices)
        passed = bool(
            job.status == "failed" and marker_present and step_failed and slice_failed
        )
        message = (
            "Expected failure propagated to Job, Step and Slice."
            if passed
            else "Expected deliberate failure did not propagate exactly as required."
        )
        return {
            "state": "pass" if passed else "fail",
            "passed": passed,
            "job": job,
            "message": message,
            "marker_present": marker_present,
            "step_failed": step_failed,
            "slice_failed": slice_failed,
            "max_slice_hosts": max_slice_hosts,
            "multi_host_slice": max_slice_hosts >= 2,
        }

    slices_successful = bool(slices) and all(
        getattr(item, "status", "") == "successful" for item in slices
    )
    passed = bool(job.status == "successful" and slices_successful)
    return {
        "state": "pass" if passed else "fail",
        "passed": passed,
        "job": job,
        "message": (
            "Validation Job and all execution Slices succeeded."
            if passed
            else "Validation Job or one of its execution Slices did not succeed."
        ),
        "max_slice_hosts": max_slice_hosts,
        "multi_host_slice": max_slice_hosts >= 2,
    }


def latest_validation_result(project, *, expected_failure=False):
    from app.models import Job

    if project is None or project.id is None:
        return evaluate_validation_job(None, expected_failure=expected_failure)
    job = (
        Job.query.filter_by(project_id=project.id)
        .order_by(Job.id.desc())
        .first()
    )
    return evaluate_validation_job(job, expected_failure=expected_failure)
