#!/usr/bin/env python3
"""Verify the active Python environment matches a Journeyman release lock."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import sys

from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def read_lock(path: Path, *, seen: set[Path] | None = None) -> dict[str, tuple[str, str]]:
    path = path.resolve()
    seen = seen or set()
    if path in seen:
        return {}
    seen.add(path)

    expected: dict[str, tuple[str, str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            referenced = line.split(None, 1)[1].strip()
            expected.update(read_lock(path.parent / referenced, seen=seen))
            continue

        match = PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(
                f"{path}: lock entry must be an exact package==version pin: {line!r}"
            )
        display_name, version = match.groups()
        expected[canonicalize_name(display_name)] = (display_name, version)

    return expected


def installed_versions() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        result[canonicalize_name(name)] = (name, distribution.version)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--postgresql",
        action="store_true",
        help="Check the PostgreSQL production lock instead of the base lock.",
    )
    args = parser.parse_args()

    lock = ROOT / (
        "requirements-postgresql.lock" if args.postgresql else "requirements.lock"
    )
    expected = read_lock(lock)
    installed = installed_versions()

    failures: list[str] = []
    for canonical_name, (display_name, expected_version) in sorted(expected.items()):
        current = installed.get(canonical_name)
        if current is None:
            failures.append(f"MISSING  {display_name}=={expected_version}")
            continue
        _, current_version = current
        if current_version != expected_version:
            failures.append(
                f"DRIFT    {display_name}: expected {expected_version}, installed {current_version}"
            )

    if failures:
        print(f"Dependency lock verification FAILED: {lock.name}")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"Dependency lock verification PASSED: {lock.name}")
    print(f"  Verified packages: {len(expected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
