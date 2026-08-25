#!/usr/bin/env python3
"""Run the Journeyman maintainer dependency vulnerability audit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "requirements-postgresql.lock"
DEFAULT_REPORT_DIR = ROOT / "dist" / "security"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the exact Journeyman production dependency lock with pip-audit "
            "and retain a dated maintainer report."
        )
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK,
        help="release lockfile to audit (default: requirements-postgresql.lock)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="directory in which to retain the dated audit report",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    lock = args.lock.resolve()
    if not lock.exists():
        print(f"Dependency lock does not exist: {lock}", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        str(lock),
        "--desc",
        "on",
    ]

    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    now = datetime.now(timezone.utc)
    if completed.returncode == 0:
        result = "CLEAN"
    elif completed.returncode == 1:
        result = "VULNERABILITIES REPORTED - MAINTAINER ASSESSMENT REQUIRED"
    else:
        result = "AUDIT ERROR - RELEASE/REVIEW GATE NOT SATISFIED"

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / (
        "dependency-audit-"
        + now.strftime("%Y%m%dT%H%M%SZ")
        + ".txt"
    )

    report = "\n".join(
        [
            "Journeyman dependency vulnerability audit",
            f"Journeyman version: {_version()}",
            f"Audit time (UTC): {now.isoformat()}",
            f"Lockfile: {lock}",
            f"Lockfile SHA-256: {_sha256(lock)}",
            f"Result: {result}",
            "",
            "Command:",
            " ".join(command),
            "",
            "pip-audit output:",
            completed.stdout.rstrip(),
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")

    print(completed.stdout, end="")
    print(f"\nAudit result: {result}")
    print(f"Report retained at: {report_path}")

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
