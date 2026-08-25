"""Build downloadable Job output archives and plain-text exports."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class JobOutputExport:
    filename: str
    mimetype: str
    data: bytes


def _text_bytes(value):
    return str(value or "").encode("utf-8")


def _safe_step_slug(value):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-")
    return (slug or "step").lower()[:80]


def _format_datetime(value):
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _job_summary(job):
    exported_at = datetime.now(timezone.utc)
    lines = [
        "Journeyman Job #{}".format(job.id),
        "Project: {}".format(job.project_name),
        "Status: {}".format(job.status),
        "Requested by: {}".format(job.requested_by),
        "Queued: {}".format(_format_datetime(job.queued_at)),
        "Started: {}".format(_format_datetime(job.started_at)),
        "Finished: {}".format(_format_datetime(job.finished_at)),
        "Exit code: {}".format(job.exit_code if job.exit_code is not None else "—"),
        "Output snapshot generated: {}".format(exported_at.isoformat()),
        "",
        "Workflow steps:",
    ]

    steps = list(job.steps)
    if not steps:
        lines.append("  (none)")
    else:
        width = max(2, len(str(max(step.position for step in steps))))
        for step in steps:
            name = step.name or "Step {}".format(step.position)
            lines.append(
                "  {position:0{width}d}. {name} — {status}".format(
                    position=step.position,
                    width=width,
                    name=name,
                    status=step.status,
                )
            )

    return "\n".join(lines).rstrip() + "\n"


def _zip_export(job):
    output = io.BytesIO()
    steps = list(job.steps)
    width = max(2, len(str(max((step.position for step in steps), default=0))))

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("job-{}.txt".format(job.id), _job_summary(job))

        for step in steps:
            directory = "step-{position:0{width}d}-{slug}/".format(
                position=step.position,
                width=width,
                slug=_safe_step_slug(step.name or "step"),
            )
            if step.stdout:
                archive.writestr(directory + "stdout.txt", _text_bytes(step.stdout))
            if step.stderr:
                archive.writestr(directory + "stderr.txt", _text_bytes(step.stderr))

    return JobOutputExport(
        filename="journeyman-job-{}-output.zip".format(job.id),
        mimetype="application/zip",
        data=output.getvalue(),
    )


def build_job_output_export(job):
    """Return the most useful output representation for a Job."""

    steps = list(job.steps)

    if len(steps) != 1:
        return _zip_export(job)

    step = steps[0]
    has_stdout = bool(step.stdout)
    has_stderr = bool(step.stderr)

    if has_stdout and not has_stderr:
        return JobOutputExport(
            filename="journeyman-job-{}-stdout.txt".format(job.id),
            mimetype="text/plain; charset=utf-8",
            data=_text_bytes(step.stdout),
        )

    if has_stderr and not has_stdout:
        return JobOutputExport(
            filename="journeyman-job-{}-stderr.txt".format(job.id),
            mimetype="text/plain; charset=utf-8",
            data=_text_bytes(step.stderr),
        )

    if has_stdout and has_stderr:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("job-{}.txt".format(job.id), _job_summary(job))
            archive.writestr("stdout.txt", _text_bytes(step.stdout))
            archive.writestr("stderr.txt", _text_bytes(step.stderr))
        return JobOutputExport(
            filename="journeyman-job-{}-output.zip".format(job.id),
            mimetype="application/zip",
            data=output.getvalue(),
        )

    return JobOutputExport(
        filename="journeyman-job-{}-output.txt".format(job.id),
        mimetype="text/plain; charset=utf-8",
        data=_text_bytes(_job_summary(job)),
    )
