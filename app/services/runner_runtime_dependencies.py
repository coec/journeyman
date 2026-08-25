"""Remote-runner runtime dependency integrity and vulnerability auditing.

Only the Python dependency closure required by the Journeyman remote-runner
agent itself is in scope here. Managed Execution Environments are deliberately
excluded and continue to own their own Ansible/Python dependency sets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from packaging.requirements import Requirement

from app import db
from app.models import Runner


RUNNER_RUNTIME_ROOT_PACKAGES = ("cryptography",)
AUDIT_STALE_AFTER = timedelta(hours=24)
MAX_AUDIT_MESSAGE = 12000


class RunnerRuntimeDependencyError(RuntimeError):
    """Raised when the canonical runner runtime cannot be determined safely."""


def _utcnow():
    return datetime.now(timezone.utc)


def _normalize_name(value):
    return str(value or "").strip().lower().replace("_", "-")


def _aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def canonical_runner_runtime_dependencies():
    """Return the exact control-plane dependency closure for the runner agent.

    The active Journeyman runtime is authoritative. Environment-marker
    requirements are evaluated for the control-plane interpreter, and optional
    extras are not requested. This intentionally excludes managed Execution
    Environments.
    """

    pending = list(RUNNER_RUNTIME_ROOT_PACKAGES)
    versions = {}

    while pending:
        requested = _normalize_name(pending.pop())
        if not requested or requested in versions:
            continue
        try:
            distribution = importlib_metadata.distribution(requested)
        except importlib_metadata.PackageNotFoundError as exc:
            raise RunnerRuntimeDependencyError(
                'Journeyman runner runtime dependency "{}" is not installed on the control plane.'.format(
                    requested
                )
            ) from exc

        canonical_name = _normalize_name(
            distribution.metadata.get("Name") or requested
        )
        versions[canonical_name] = str(distribution.version)

        for requirement_text in distribution.requires or ():
            try:
                requirement = Requirement(requirement_text)
            except Exception as exc:
                raise RunnerRuntimeDependencyError(
                    'Unable to parse dependency metadata for "{}": {}.'.format(
                        canonical_name, requirement_text
                    )
                ) from exc
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            dependency_name = _normalize_name(requirement.name)
            if dependency_name and dependency_name not in versions:
                pending.append(dependency_name)

    return dict(sorted(versions.items()))


def canonical_runner_runtime_requirements():
    return [
        "{}=={}".format(name, version)
        for name, version in canonical_runner_runtime_dependencies().items()
    ]


def canonical_runner_runtime_package_names():
    return list(canonical_runner_runtime_dependencies())


def runner_runtime_dependency_names_for_reporting():
    """Return the best package-name set a runner should report.

    If the control plane itself is missing a required runtime package, keep
    heartbeats functional by asking for the direct roots. The integrity state
    will separately report the control-plane configuration error.
    """

    try:
        return canonical_runner_runtime_package_names()
    except RunnerRuntimeDependencyError:
        return list(RUNNER_RUNTIME_ROOT_PACKAGES)


def _normalized_reported_dependencies(payload):
    if not isinstance(payload, dict):
        raise ValueError("runtime_dependencies must be an object.")

    expected_names = set(runner_runtime_dependency_names_for_reporting())
    normalized = {}
    for raw_name, raw_version in payload.items():
        name = _normalize_name(raw_name)
        if name not in expected_names:
            # Older/newer runners may report a superset. Retain only the
            # canonical control-plane closure so Execution Environments and
            # unrelated site packages can never leak into this feature.
            continue
        version = str(raw_version or "").strip()
        if not version or len(version) > 120:
            raise ValueError(
                'runtime dependency version for "{}" is invalid.'.format(name)
            )
        normalized[name] = version
    return dict(sorted(normalized.items()))


def dependency_fingerprint(dependencies):
    payload = json.dumps(
        dict(sorted((str(k), str(v)) for k, v in dependencies.items())),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def set_reported_runner_runtime_dependencies(runner, payload, *, now=None):
    """Persist the runner's observed versions and invalidate stale audit data."""

    observed = _normalized_reported_dependencies(payload)
    serialized = json.dumps(observed, sort_keys=True, separators=(",", ":"))
    changed = serialized != (runner.runtime_dependencies_json or "{}")
    runner.runtime_dependencies_json = serialized
    runner.runtime_dependencies_reported_at = now or _utcnow()
    if changed:
        runner.runtime_dependency_audit_status = "pending"
        runner.runtime_dependency_audit_message = "Dependency versions changed; audit pending."
        runner.runtime_dependency_audit_checked_at = None
        runner.runtime_dependency_audit_fingerprint = ""
        runner.runtime_dependency_audit_json = "{}"
    return observed


def reported_runner_runtime_dependencies(runner):
    try:
        value = json.loads(runner.runtime_dependencies_json or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        _normalize_name(name): str(version)
        for name, version in value.items()
        if _normalize_name(name) and str(version or "").strip()
    }


def runner_runtime_dependency_state(runner):
    """Return integrity/audit data suitable for the Runners UI."""

    try:
        expected = canonical_runner_runtime_dependencies()
    except RunnerRuntimeDependencyError as exc:
        return {
            "state": "unknown",
            "label": "Unknown",
            "message": str(exc),
            "drift": [],
            "expected": {},
            "reported": reported_runner_runtime_dependencies(runner),
            "audit_status": "error",
            "audit_label": "Audit unavailable",
            "audit_message": str(exc),
            "audit_findings_count": 0,
            "audit_checked_at": None,
        }

    if runner.is_local:
        reported = dict(expected)
        state = "canonical"
        label = "Canonical"
        message = "Canonical Journeyman runner runtime on this control plane."
        drift = []
    else:
        reported = reported_runner_runtime_dependencies(runner)
        if not reported:
            state = "unknown"
            label = "Not reported"
            message = "Update the remote runner so it can report runtime dependency versions."
            drift = []
        else:
            drift = []
            for name, expected_version in expected.items():
                actual = reported.get(name)
                if actual != expected_version:
                    drift.append({
                        "name": name,
                        "expected": expected_version,
                        "reported": actual,
                    })
            if drift:
                state = "drifted"
                label = "Drifted ({})".format(len(drift))
                message = "Runner runtime dependency versions differ from the control plane."
            else:
                state = "current"
                label = "Current"
                message = "Runner runtime dependencies match the control plane."

    audit_status = str(runner.runtime_dependency_audit_status or "unknown")
    audit_labels = {
        "clean": "Clean",
        "findings": "Findings",
        "pending": "Audit pending",
        "error": "Audit unavailable",
        "unknown": "Not audited",
    }
    try:
        audit_json = json.loads(runner.runtime_dependency_audit_json or "{}")
    except (TypeError, ValueError):
        audit_json = {}
    if not isinstance(audit_json, dict):
        audit_json = {}

    return {
        "state": state,
        "label": label,
        "message": message,
        "drift": drift,
        "expected": expected,
        "reported": reported,
        "audit_status": audit_status,
        "audit_label": audit_labels.get(audit_status, "Unknown"),
        "audit_message": str(runner.runtime_dependency_audit_message or ""),
        "audit_findings_count": int(audit_json.get("finding_count") or 0),
        "audit_checked_at": runner.runtime_dependency_audit_checked_at,
    }


def runner_runtime_dependency_update_required(runner):
    return (
        not runner.is_local
        and runner_runtime_dependency_state(runner)["state"] == "drifted"
    )


def _audit_exact_dependencies(dependencies):
    """Audit an exact package/version set centrally with pip-audit."""

    requirements = [
        "{}=={}".format(name, version)
        for name, version in sorted(dependencies.items())
    ]
    if not requirements:
        return {
            "status": "error",
            "message": "No runner runtime dependencies were available to audit.",
            "details": {},
        }

    with tempfile.TemporaryDirectory(prefix="journeyman-runner-audit-") as tmpdir:
        requirement_path = Path(tmpdir) / "requirements.txt"
        requirement_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "pip_audit",
            "--no-deps",
            "--disable-pip",
            "--progress-spinner",
            "off",
            "--format",
            "json",
            "-r",
            str(requirement_path),
        ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": "pip-audit timed out while auditing runner runtime dependencies.",
                "details": {},
            }

    if completed.returncode not in (0, 1):
        output = (completed.stderr or completed.stdout or "").strip()
        if "No module named pip_audit" in output:
            output = "pip-audit is not installed in the Journeyman control-plane Python environment."
        return {
            "status": "error",
            "message": (output or "pip-audit returned an unexpected error.")[:MAX_AUDIT_MESSAGE],
            "details": {},
        }

    try:
        payload = json.loads(completed.stdout or "{}")
    except (TypeError, ValueError):
        return {
            "status": "error",
            "message": "pip-audit returned invalid JSON output.",
            "details": {},
        }

    dependencies_payload = payload.get("dependencies", []) if isinstance(payload, dict) else []
    findings = []
    for dependency in dependencies_payload if isinstance(dependencies_payload, list) else []:
        if not isinstance(dependency, dict):
            continue
        package_name = _normalize_name(dependency.get("name"))
        package_version = str(dependency.get("version") or "")
        for vuln in dependency.get("vulns") or []:
            if not isinstance(vuln, dict):
                continue
            findings.append({
                "package": package_name,
                "version": package_version,
                "id": str(vuln.get("id") or ""),
                "fix_versions": [str(value) for value in vuln.get("fix_versions") or []],
            })

    finding_count = len(findings)
    if finding_count:
        affected = sorted({item["package"] for item in findings if item["package"]})
        message = "{} known vulnerabilit{} across {} runner runtime package{}.".format(
            finding_count,
            "y" if finding_count == 1 else "ies",
            len(affected),
            "" if len(affected) == 1 else "s",
        )
        status = "findings"
    else:
        message = "No known vulnerabilities reported for this runner runtime dependency set."
        status = "clean"

    return {
        "status": status,
        "message": message,
        "details": {
            "finding_count": finding_count,
            "findings": findings[:100],
        },
    }


def _audit_due(runner, fingerprint, now):
    if runner.runtime_dependency_audit_fingerprint != fingerprint:
        return True
    checked = _aware_utc(runner.runtime_dependency_audit_checked_at)
    if checked is None:
        return True
    return checked <= now - AUDIT_STALE_AFTER


def refresh_runner_runtime_dependency_audits(*, now=None, force=False):
    """Audit observed runner runtime dependency sets centrally.

    Matching dependency fingerprints are audited once per pass and the result is
    shared across matching runners. This is intentionally independent of
    managed Execution Environments.
    """

    now = now or _utcnow()
    try:
        canonical = canonical_runner_runtime_dependencies()
    except RunnerRuntimeDependencyError as exc:
        message = str(exc)
        changed = []
        for runner in Runner.query.all():
            runner.runtime_dependency_audit_status = "error"
            runner.runtime_dependency_audit_message = message
            runner.runtime_dependency_audit_checked_at = now
            runner.runtime_dependency_audit_fingerprint = ""
            runner.runtime_dependency_audit_json = "{}"
            changed.append(runner.id)
        db.session.commit()
        return {"audited": changed, "clean": 0, "findings": 0, "errors": len(changed)}

    results_by_fingerprint = {}
    counts = {"audited": [], "clean": 0, "findings": 0, "errors": 0}

    for runner in Runner.query.order_by(Runner.id).all():
        observed = canonical if runner.is_local else reported_runner_runtime_dependencies(runner)
        missing = [name for name in canonical if name not in observed]
        if missing:
            runner.runtime_dependency_audit_status = "error"
            runner.runtime_dependency_audit_message = (
                "Cannot audit runner runtime: missing reported package{} {}.".format(
                    "s" if len(missing) != 1 else "",
                    ", ".join(missing),
                )
            )
            runner.runtime_dependency_audit_checked_at = now
            runner.runtime_dependency_audit_fingerprint = ""
            runner.runtime_dependency_audit_json = "{}"
            counts["audited"].append(runner.id)
            counts["errors"] += 1
            continue

        filtered = {name: observed[name] for name in canonical}
        fingerprint = dependency_fingerprint(filtered)
        if not force and not _audit_due(runner, fingerprint, now):
            continue

        result = results_by_fingerprint.get(fingerprint)
        if result is None:
            result = _audit_exact_dependencies(filtered)
            results_by_fingerprint[fingerprint] = result

        runner.runtime_dependency_audit_status = result["status"]
        runner.runtime_dependency_audit_message = result["message"][:MAX_AUDIT_MESSAGE]
        runner.runtime_dependency_audit_checked_at = now
        runner.runtime_dependency_audit_fingerprint = fingerprint
        runner.runtime_dependency_audit_json = json.dumps(
            result.get("details") or {}, sort_keys=True, separators=(",", ":")
        )
        counts["audited"].append(runner.id)
        if result["status"] == "clean":
            counts["clean"] += 1
        elif result["status"] == "findings":
            counts["findings"] += 1
        else:
            counts["errors"] += 1

    db.session.commit()
    return counts
