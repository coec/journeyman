"""Short-lived, filesystem-backed progress for interactive dispatches.

Progress is deliberately kept outside the application database so updates can
be published while the Job transaction is still being assembled.  All
Gunicorn workers share the instance directory, allowing the browser's SSE
request to observe work being performed by a different worker.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

from flask import current_app


_PROGRESS_ID = re.compile(r"^[A-Za-z0-9-]{20,80}$")
_PROGRESS_TTL = timedelta(hours=1)


def valid_progress_id(value):
    return bool(_PROGRESS_ID.fullmatch(str(value or "").strip()))


def _root():
    path = Path(current_app.instance_path) / "dispatch-progress"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _path(progress_id):
    if not valid_progress_id(progress_id):
        return None
    return _root() / (str(progress_id) + ".json")


def _utcnow():
    return datetime.now(timezone.utc)


def _cleanup():
    cutoff = _utcnow() - _PROGRESS_TTL
    try:
        entries = tuple(_root().glob("*.json"))
    except OSError:
        return

    for entry in entries:
        try:
            modified = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


def _write(progress_id, payload):
    path = _path(progress_id)
    if path is None:
        return

    payload = dict(payload)
    payload["updated_at"] = _utcnow().isoformat()
    temporary = path.with_suffix(".tmp")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)

    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_dispatch_progress(progress_id):
    path = _path(progress_id)
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


class DispatchProgressReporter:
    def __init__(self, progress_id, owner, label):
        self.progress_id = str(progress_id or "").strip()
        self.owner = str(owner or "").strip()
        self.label = str(label or "Dispatch").strip() or "Dispatch"
        self.sequence = 0
        self.enabled = valid_progress_id(self.progress_id) and bool(self.owner)
        if self.enabled:
            _cleanup()
            self._publish("active", "starting", "Starting dispatch")

    def _publish(self, state, phase, message, detail="", job_id=None):
        if not self.enabled:
            return
        self.sequence += 1
        _write(
            self.progress_id,
            {
                "owner": self.owner,
                "label": self.label,
                "sequence": self.sequence,
                "state": state,
                "phase": str(phase or ""),
                "message": str(message or ""),
                "detail": str(detail or ""),
                "job_id": job_id,
            },
        )

    def __call__(self, phase, message, detail=""):
        self._publish("active", phase, message, detail)

    def done(self, message="Dispatch ready", *, job_id=None):
        self._publish("done", "complete", message, job_id=job_id)

    def fail(self, message):
        self._publish("error", "failed", str(message or "Dispatch failed"))


def dispatch_progress_reporter(progress_id, owner, label):
    return DispatchProgressReporter(progress_id, owner, label)
