#!/bin/bash

set -euo pipefail

usage() {
    echo "Usage: ${0} <job_id> [--full]"
}

if [ "${#}" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage
    exit 1
fi

JOB_ID="${1}"
MODE="${2:-}"

if [[ ! "${JOB_ID}" =~ ^[0-9]+$ ]]; then
    echo "Error: Job ID '${JOB_ID}' is not a valid number." >&2
    exit 1
fi

if [[ -n "${MODE}" && "${MODE}" != "--full" ]]; then
    usage
    exit 1
fi

python - "${JOB_ID}" "${MODE}" <<'PY'
import sys
from app import create_app, db
from app.models import Job

job_id = int(sys.argv[1])
full = len(sys.argv) > 2 and sys.argv[2] == "--full"

MAX_OUTPUT = 4000


def show_value(label, value, indent="  "):
    print(f"{indent}{label:<8}: {value}")


def show_text(label, value, indent="  "):
    value = value or ""

    print(f"{indent}{label}:")

    if not value:
        print(f"{indent}  <empty>")
        return

    if full or len(value) <= MAX_OUTPUT:
        for line in value.rstrip().splitlines():
            print(f"{indent}  {line}")
        return

    truncated = value[:MAX_OUTPUT]
    for line in truncated.rstrip().splitlines():
        print(f"{indent}  {line}")

    print(
        f"{indent}  ... truncated "
        f"({len(value) - MAX_OUTPUT} more characters; use --full)"
    )


app = create_app()

with app.app_context():
    job = db.session.get(Job, job_id)

    if not job:
        print(f"Error: Job {job_id} not found.")
        sys.exit(1)

    print(f"JOB {job.id}")
    show_value("status", job.status)
    show_value("rc", job.exit_code)
    show_value("message", job.message or "")

    for step in job.steps:
        print()
        print(f"STEP {step.position}: {getattr(step, 'name', '') or ''}")

        show_value("status", step.status)
        show_value("rc", step.exit_code)
        show_value("command", step.command or "")
        show_text("stdout", step.stdout)
        show_text("stderr", step.stderr)

        slices = list(step.execution_slices)

        if not slices:
            print("  execution slices: none")
            continue

        for s in slices:
            print()
            print(f"  SLICE {s.id}")

            show_value("status", s.status, indent="    ")
            show_value(
                "runner",
                s.runner_hostname or s.runner_name or "",
                indent="    ",
            )
            show_value("rc", s.exit_code, indent="    ")
            show_value("message", s.message or "", indent="    ")
            show_value("command", s.command or "", indent="    ")
            show_text("stdout", s.stdout, indent="    ")
            show_text("stderr", s.stderr, indent="    ")
PY
