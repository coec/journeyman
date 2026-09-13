"""Journeyman application and bundled component version helpers."""

from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_VERSION_FILE = REPOSITORY_ROOT / "VERSION"
REMOTE_RUNNER_PATH = REPOSITORY_ROOT / "bin" / "journeyman-remote-runner"


def read_journeyman_version():
    """Return the Journeyman application version from the repository VERSION file."""

    try:
        version = APPLICATION_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


def read_bundled_remote_runner_version():
    """Return VERSION declared by the exact bundled remote-runner artifact.

    The remote runner is intentionally self-contained and is copied to systems
    that do not have the Journeyman application package installed.  Treat the
    artifact itself as authoritative instead of duplicating its version in the
    controller source tree.
    """

    try:
        source = REMOTE_RUNNER_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "Unable to read bundled remote runner {!s}.".format(REMOTE_RUNNER_PATH)
        ) from exc

    try:
        module = ast.parse(source, filename=str(REMOTE_RUNNER_PATH))
    except SyntaxError as exc:
        raise RuntimeError(
            "Bundled remote runner {!s} is not valid Python.".format(REMOTE_RUNNER_PATH)
        ) from exc

    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "VERSION" for target in targets):
            continue
        value_node = node.value
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            version = value_node.value.strip()
            if version:
                return version
        break

    raise RuntimeError(
        "Bundled remote runner {!s} does not declare a string VERSION.".format(
            REMOTE_RUNNER_PATH
        )
    )


JOURNEYMAN_VERSION = read_journeyman_version()
REMOTE_RUNNER_VERSION = read_bundled_remote_runner_version()
