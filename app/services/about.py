"""Runtime version information used by the About dialog."""

from functools import lru_cache
from importlib import metadata
from pathlib import Path
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT_FILES = (
    ROOT / "requirements.txt",
    ROOT / "requirements-postgresql.txt",
)


def _direct_requirement_names():
    names = set()
    visited = set()

    def read_file(path):
        path = Path(path).resolve()
        if path in visited or not path.exists():
            return
        visited.add(path)

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ")):
                referenced = line.split(None, 1)[1].strip()
                read_file(path.parent / referenced)
                continue

            # Requirements files used by Journeyman do not contain direct URL
            # requirements. Requirement() correctly handles version ranges,
            # extras, and environment markers.
            try:
                requirement = Requirement(line)
            except Exception:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            names.add(canonicalize_name(requirement.name))

    for path in REQUIREMENT_FILES:
        read_file(path)

    return names


@lru_cache(maxsize=1)
def runtime_dependency_inventory():
    """Return the installed direct/transitive dependency closure for Journeyman."""

    installed = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        installed[canonicalize_name(name)] = distribution

    direct_names = _direct_requirement_names()
    pending = list(sorted(direct_names))
    seen = set()
    rows = []

    while pending:
        canonical_name = pending.pop(0)
        if canonical_name in seen:
            continue
        seen.add(canonical_name)

        distribution = installed.get(canonical_name)
        if distribution is None:
            rows.append(
                {
                    "name": canonical_name,
                    "version": "not installed",
                    "direct": True,
                }
            )
            continue

        display_name = distribution.metadata.get("Name") or canonical_name
        rows.append(
            {
                "name": display_name,
                "version": distribution.version,
                "direct": canonical_name in direct_names,
            }
        )

        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except Exception:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            dependency_name = canonicalize_name(requirement.name)
            if dependency_name not in seen:
                pending.append(dependency_name)

    rows.sort(key=lambda row: row["name"].casefold())
    return tuple(rows)


def runtime_python_version():
    return ".".join(str(part) for part in sys.version_info[:3])
