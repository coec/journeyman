"""Resolve a repository snapshot's Ansible Galaxy role requirements.

Roles are installed in a job-private directory, never in the service account's
shared ~/.ansible/roles.  Ansible's ``roles/requirements.yml`` convention is
used by AAP Project updates and supported here for Git-sourced roles as well.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from flask import current_app

from app.services.outbound_security import (
    OutboundSecurityError,
    _is_self_destination,
    validate_repository_url,
)


class RoleDependencyError(RuntimeError):
    """Repository role requirements could not be resolved safely."""


_ROLE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_SCP_GIT_SOURCE = re.compile(
    r"^(?:[A-Za-z0-9._-]+@)?(?P<hostname>[^/:@\s]+):[^\s]+$"
)


def _validate_git_role_source(source):
    """Reject local/insecure transports even with optional outbound checks off.

    In particular, ``file:///path`` must never be confused with Git's
    ``hostname:path`` SSH shorthand by the general repository validator.
    """
    lower = source.lower()
    if lower.startswith(("https://", "ssh://")):
        try:
            hostname = urlsplit(source).hostname
        except ValueError as exc:
            raise RoleDependencyError("Invalid role Git source.") from exc
    elif "://" in source:
        raise RoleDependencyError("Role Git sources must use HTTPS or SSH.")
    else:
        match = _SCP_GIT_SOURCE.fullmatch(source)
        if not match:
            raise RoleDependencyError("Role Git sources must use HTTPS or SSH.")
        hostname = match.group("hostname")

    try:
        validate_repository_url(source)
    except OutboundSecurityError as exc:
        raise RoleDependencyError(
            "Role Git source is not permitted: {}".format(exc)
        ) from exc
    if not hostname or _is_self_destination(hostname):
        raise RoleDependencyError(
            "Role Git source must not point to the Journeyman host or loopback."
        )



def read_role_requirements(contents):
    """Validate role names and Git destinations before invoking Galaxy.

    Role requirement files come from project source control, which is not
    trusted to extend Journeyman's outbound destination policy.
    """
    try:
        parsed = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise RoleDependencyError("Invalid roles/requirements.yml YAML.") from exc

    if parsed is None:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("roles", [])
    if not isinstance(parsed, list):
        raise RoleDependencyError("roles/requirements.yml must contain a list of roles.")

    for item in parsed:
        if isinstance(item, str):
            src, scm, name = item, None, item
        elif isinstance(item, dict):
            src = item.get("src")
            scm = item.get("scm")
            name = item.get("name")
        else:
            raise RoleDependencyError("Invalid role entry in roles/requirements.yml.")
        if not isinstance(src, str) or not src.strip():
            raise RoleDependencyError("Each role requires a non-empty src value.")
        src = src.strip()
        if scm is not None and scm != "git":
            raise RoleDependencyError("Only Git SCM roles and Galaxy role names are supported.")

        is_git = scm == "git" or src.startswith(("git@", "ssh://", "https://", "http://"))
        if is_git:
            _validate_git_role_source(src)
            if scm not in (None, "git"):
                raise RoleDependencyError("Unsupported role SCM.")
            if not name:
                raise RoleDependencyError("Git roles must specify a safe installation name.")
        elif not _ROLE_NAME.fullmatch(src):
            raise RoleDependencyError("Unsupported Galaxy role name: {!r}".format(src))

        if name is not None and (
            not isinstance(name, str)
            or not _ROLE_NAME.fullmatch(name)
            or name in (".", "..")
        ):
            raise RoleDependencyError("Role name must be a safe directory name.")
    return parsed


def install_repository_roles(requirements_path, target_roles):
    """Install role dependencies into the given job-private roles directory.

    Return True when a non-empty requirements file was processed.  Private SSH
    sources use the configured Git SSH identity of the service account.
    """
    requirements_path = Path(requirements_path)
    if requirements_path.is_symlink():
        raise RoleDependencyError("Symlinked role requirements files are not supported.")
    if not requirements_path.is_file():
        return False
    try:
        requirements = read_role_requirements(requirements_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise RoleDependencyError("Unable to read roles/requirements.yml.") from exc
    if not requirements:
        return False

    executable = str(
        current_app.config.get("RUNNER_ROLE_GALAXY_EXECUTABLE") or "ansible-galaxy"
    )
    if not shutil.which(executable):
        raise RoleDependencyError(
            "ansible-galaxy is required on the Journeyman controller to resolve Project roles."
        )
    target_roles = Path(target_roles)
    if target_roles.is_symlink():
        raise RoleDependencyError("Symlinked role directories are not supported.")
    target_roles.mkdir(mode=0o700, parents=True, exist_ok=True)
    timeout = max(1, int(current_app.config.get("RUNNER_ROLE_INSTALL_TIMEOUT_SECONDS", 900)))
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_COUNT"] = "1"
    environment["GIT_CONFIG_KEY_0"] = "http.followRedirects"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    try:
        result = subprocess.run(
            [executable, "role", "install", "-r", str(requirements_path),
             "-p", str(target_roles)],
            cwd=str(requirements_path.parent),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RoleDependencyError(
            "Installing repository roles exceeded {} seconds.".format(timeout)
        ) from exc
    except OSError as exc:
        raise RoleDependencyError("Unable to run ansible-galaxy role install.") from exc
    if result.returncode:
        # Galaxy stderr can contain embedded URLs/credentials from other
        # sources. Avoid echoing it into the runner API or job logs.
        raise RoleDependencyError(
            "ansible-galaxy role install failed (exit {}). Check controller "
            "GitLab SSH access and the roles/requirements.yml dependencies."
            .format(result.returncode)
        )
    return True
