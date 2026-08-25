"""Execution-environment discovery, validation, and managed creation."""

import os
import re
import shutil
import subprocess
import sys
from packaging.requirements import InvalidRequirement, Requirement
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app import db
from app.models.environment import Environment
from app.services.environment_build_settings import build_proxy_environment, redact_proxy_secrets


class EnvironmentValidationError(Exception):
    pass


class EnvironmentBuildError(Exception):
    pass


SYSTEM_ENVIRONMENT_PATH = "__SYSTEM_ANSIBLE__"
SYSTEM_ENVIRONMENT_NAME = "System Ansible"
APPLICATION_ENVIRONMENT_NAME = "Journeyman application environment"


_COLLECTION_SPEC_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+(?::[A-Za-z0-9*+_.-]+)?$")
_SYSTEM_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_.:@-]*$")


def application_environment_path():
    configured = current_app.config.get("APPLICATION_ENVIRONMENT_PATH")
    return str(Path(configured or sys.prefix).resolve())




def is_application_environment(environment):
    """Return whether ``environment`` is Journeyman's own application runtime."""

    return bool(
        environment
        and str(environment.name or "").strip() == APPLICATION_ENVIRONMENT_NAME
    )

def managed_environment_root():
    configured = current_app.config.get("MANAGED_ENVIRONMENT_ROOT", "/opt/journeyman/environments")
    return Path(configured).expanduser().resolve()


def allowed_python_interpreters():
    configured = current_app.config.get("ENVIRONMENT_PYTHON_INTERPRETERS", "")
    values = [item.strip() for item in str(configured).split(",") if item.strip()]
    if not values:
        values = [sys.executable]
    result = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if str(path) not in result:
            result.append(str(path))
    return result


def ensure_builtin_environment():
    """Ensure the built-in system and application execution environments exist."""
    application_path = application_environment_path()
    application_environment = Environment.query.filter_by(
        name=APPLICATION_ENVIRONMENT_NAME
    ).first()
    if application_environment is None:
        application_environment = Environment(
            name=APPLICATION_ENVIRONMENT_NAME,
            path=application_path,
            enabled=True,
            is_default=False,
            is_builtin=True,
        )
        db.session.add(application_environment)
    elif application_environment.path != application_path:
        application_environment.path = application_path
        application_environment.validation_status = "not_tested"

    system_environment = Environment.query.filter_by(
        name=SYSTEM_ENVIRONMENT_NAME
    ).first()
    if system_environment is None:
        system_environment = Environment(
            name=SYSTEM_ENVIRONMENT_NAME,
            path=SYSTEM_ENVIRONMENT_PATH,
            enabled=True,
            is_default=False,
            is_builtin=True,
        )
        db.session.add(system_environment)

    current_default = Environment.query.filter_by(is_default=True).first()
    if current_default is None or current_default.name == APPLICATION_ENVIRONMENT_NAME:
        Environment.query.update({Environment.is_default: False})
        system_environment.is_default = True

    db.session.commit()

    for environment in (system_environment, application_environment):
        needs_validation = environment.validation_status == "not_tested"
        if environment is application_environment and (
            environment.validation_status != "passed"
            or bool(environment.ansible_version)
        ):
            # Older releases incorrectly validated the application runtime as
            # an Ansible execution Environment. Reclassify that stored state.
            needs_validation = True
        if needs_validation:
            validate_environment(environment)

    return system_environment


def _run(command, *, timeout=900, use_build_proxy=False, proxy_credential=None):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=(build_proxy_environment(os.environ, proxy_credential=proxy_credential) if use_build_proxy else {**os.environ, "PATH": os.environ.get("PATH", "")}),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentBuildError(str(exc)) from exc
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    output = redact_proxy_secrets(output, proxy_credential=proxy_credential)
    if completed.returncode != 0:
        raise EnvironmentBuildError(output or f"Command exited with {completed.returncode}.")
    return output


def _run_version(command):
    output = _run(command, timeout=15)
    return output.splitlines()[0] if output else "Unknown"


def _validate_package_spec(item, label):
    if not item or item.startswith("-") or "://" in item or item.startswith(("git+", "file:")):
        raise EnvironmentBuildError(f'Invalid {label} specification: "{item}".')
    try:
        requirement = Requirement(item)
    except InvalidRequirement as exc:
        raise EnvironmentBuildError(f'Invalid {label} specification: "{item}".') from exc
    if requirement.url is not None:
        raise EnvironmentBuildError(f'URLs are not permitted in {label} specifications.')
    return str(requirement)


def _normalise_lines(value, regex, label):
    result = []
    for raw in (value or "").splitlines():
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        if regex is None:
            item = _validate_package_spec(item, label)
        elif not regex.fullmatch(item):
            raise EnvironmentBuildError(f'Invalid {label} specification: "{item}".')
        result.append(item)
    return result


def managed_environment_path(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise EnvironmentBuildError("Environment name does not produce a usable directory name.")
    root = managed_environment_root()
    path = (root / slug).resolve()
    if root not in path.parents:
        raise EnvironmentBuildError("Managed environment path escaped the configured root.")
    return path


def prepare_managed_environment_build(
    environment,
    *,
    python_interpreter,
    ansible_spec,
    pip_requirements="",
    system_requirements="",
    collections="",
):
    """Validate and persist a managed environment build request without running it."""
    allowed = allowed_python_interpreters()
    interpreter = str(Path(python_interpreter).expanduser().resolve())
    if interpreter not in allowed:
        raise EnvironmentBuildError("The selected Python interpreter is not allowed by Journeyman configuration.")
    if not Path(interpreter).is_file() or not os.access(interpreter, os.X_OK):
        raise EnvironmentBuildError(f"Python interpreter is not executable: {interpreter}")

    ansible_spec = _validate_package_spec((ansible_spec or "ansible-core").strip(), "ansible-core package")
    packages = _normalise_lines(pip_requirements, None, "Python package")
    system_packages = _normalise_lines(
        system_requirements,
        _SYSTEM_PACKAGE_RE,
        "runner system package",
    )
    collection_specs = _normalise_lines(collections, _COLLECTION_SPEC_RE, "Ansible collection")

    final_path = Path(environment.path).resolve()
    root = managed_environment_root()
    if root not in final_path.parents:
        raise EnvironmentBuildError("Journeyman-managed environments must be below the configured managed root.")

    environment.python_interpreter = interpreter
    environment.ansible_spec = ansible_spec
    environment.pip_requirements = "\n".join(packages)
    environment.system_requirements = "\n".join(system_packages)
    environment.collection_requirements = "\n".join(collection_specs)
    environment.build_status = "queued"
    environment.build_message = "Waiting for the environment builder."
    environment.validation_status = "not_tested"
    environment.validation_message = "Build queued."
    db.session.commit()
    return environment


def prepare_registered_environment_update(
    environment,
    *,
    pip_requirements="",
    system_requirements="",
    collections="",
):
    """Validate and persist dependency updates for a registered virtual environment."""
    root = Path(environment.path).expanduser().resolve()
    python_path = root / "bin" / "python"
    galaxy_path = root / "bin" / "ansible-galaxy"
    if not root.is_dir():
        raise EnvironmentBuildError(f"Directory does not exist: {root}")
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise EnvironmentBuildError(f"Executable Python was not found: {python_path}")

    packages = _normalise_lines(pip_requirements, None, "Python package")
    system_packages = _normalise_lines(
        system_requirements,
        _SYSTEM_PACKAGE_RE,
        "runner system package",
    )
    collection_specs = _normalise_lines(collections, _COLLECTION_SPEC_RE, "Ansible collection")
    if collection_specs and (not galaxy_path.is_file() or not os.access(galaxy_path, os.X_OK)):
        raise EnvironmentBuildError(f"Executable ansible-galaxy was not found: {galaxy_path}")

    environment.pip_requirements = "\n".join(packages)
    environment.system_requirements = "\n".join(system_packages)
    environment.collection_requirements = "\n".join(collection_specs)
    environment.build_status = "queued"
    environment.build_message = "Waiting for the environment builder to update dependencies."
    environment.validation_status = "not_tested"
    environment.validation_message = "Dependency update queued."
    db.session.commit()
    return environment


def update_registered_environment(environment):
    """Install declared dependencies into an existing registered virtual environment."""
    if environment.is_managed or environment.is_builtin:
        raise EnvironmentBuildError("Only registered virtual environments can be updated in-place.")

    root = Path(environment.path).expanduser().resolve()
    python_path = root / "bin" / "python"
    packages = [item for item in (environment.pip_requirements or "").splitlines() if item]
    collection_specs = [item for item in (environment.collection_requirements or "").splitlines() if item]

    environment.build_status = "building"
    environment.build_message = "Updating registered environment dependencies."
    db.session.commit()

    output_parts = []
    try:
        if packages:
            output_parts.append(
                _run([str(python_path), "-m", "pip", "install", *packages], use_build_proxy=True, proxy_credential=environment.proxy_credential)
            )
        if collection_specs:
            galaxy_path = root / "bin" / "ansible-galaxy"
            output_parts.append(
                _run([str(galaxy_path), "collection", "install", *collection_specs], use_build_proxy=True, proxy_credential=environment.proxy_credential)
            )

        if not validate_environment(environment):
            raise EnvironmentBuildError(environment.validation_message or "Environment validation failed.")

        environment.build_status = "passed"
        environment.build_message = redact_proxy_secrets(
            "\n".join(part for part in output_parts if part),
            proxy_credential=environment.proxy_credential,
        )[-12000:] or "Registered environment dependencies are up to date."
        db.session.commit()
        return True
    except Exception as exc:
        environment.build_status = "failed"
        combined = "\n".join(part for part in output_parts if part)
        environment.build_message = redact_proxy_secrets(
            (combined + "\n" + str(exc)).strip(),
            proxy_credential=environment.proxy_credential,
        )[-12000:]
        environment.validation_status = "failed"
        environment.validation_message = "Registered environment dependency update failed."
        db.session.commit()
        return False


def create_managed_environment(environment):
    """Build or rebuild a managed environment at its final path.

    Python virtual environments are not relocatable: console-script shebangs
    contain the path used during installation. Build directly at the final
    path, retaining the previous environment as a rollback copy until the new
    environment validates successfully.
    """
    interpreter = environment.python_interpreter
    ansible_spec = environment.ansible_spec or "ansible-core"
    packages = [item for item in (environment.pip_requirements or "").splitlines() if item]
    collection_specs = [item for item in (environment.collection_requirements or "").splitlines() if item]

    final_path = Path(environment.path).resolve()
    root = managed_environment_root()
    if root not in final_path.parents:
        raise EnvironmentBuildError("Journeyman-managed environments must be below the configured managed root.")

    previous_path = final_path.with_name(f".{final_path.name}.previous-{environment.id}")
    if previous_path.exists():
        shutil.rmtree(previous_path)

    environment.build_status = "building"
    environment.build_message = "Creating virtual environment."
    db.session.commit()

    output_parts = []
    had_previous = final_path.exists()
    try:
        root.mkdir(parents=True, exist_ok=True)
        if had_previous:
            final_path.rename(previous_path)

        output_parts.append(_run([interpreter, "-m", "venv", str(final_path)]))
        python_path = final_path / "bin" / "python"
        environment.build_message = "Installing Python packages."
        db.session.commit()
        output_parts.append(_run([str(python_path), "-m", "pip", "install", ansible_spec, *packages], use_build_proxy=True, proxy_credential=environment.proxy_credential))
        if collection_specs:
            environment.build_message = "Installing Ansible collections."
            db.session.commit()
            galaxy_path = final_path / "bin" / "ansible-galaxy"
            output_parts.append(_run([str(galaxy_path), "collection", "install", *collection_specs], use_build_proxy=True, proxy_credential=environment.proxy_credential))

        if not validate_environment(environment):
            raise EnvironmentBuildError(environment.validation_message or "Environment validation failed.")

        if previous_path.exists():
            shutil.rmtree(previous_path)

        environment.build_status = "passed"
        environment.build_message = redact_proxy_secrets("\n".join(part for part in output_parts if part), proxy_credential=environment.proxy_credential)[-12000:] or "Managed environment built successfully."
        db.session.commit()
        return True
    except Exception as exc:
        if final_path.exists():
            shutil.rmtree(final_path, ignore_errors=True)
        if previous_path.exists():
            previous_path.rename(final_path)
            validate_environment(environment)
        environment.build_status = "failed"
        combined = "\n".join(part for part in output_parts if part)
        environment.build_message = redact_proxy_secrets((combined + "\n" + str(exc)).strip(), proxy_credential=environment.proxy_credential)[-12000:]
        if not had_previous:
            environment.validation_status = "failed"
            environment.validation_message = "Environment build failed."
        db.session.commit()
        return False

def environment_system_requirements(environment):
    """Return validated runner-side RPM/DNF package requirements."""

    return [
        line.strip()
        for line in str(getattr(environment, "system_requirements", "") or "").splitlines()
        if line.strip()
    ]


def required_runner_environment_system_packages():
    """Return Environment system packages required by enabled execution Environments.

    Remote-runner management runs with the bootstrap privilege path, so it is
    responsible for installing these host packages.  Environment sync remains
    deliberately unprivileged.
    """

    environments = (
        Environment.query
        .filter(
            Environment.enabled.is_(True),
            Environment.is_builtin.is_(False),
            Environment.name != APPLICATION_ENVIRONMENT_NAME,
        )
        .all()
    )
    packages = set()
    for environment in environments:
        if environment.validation_status != "passed":
            continue
        if environment.is_managed and environment.build_status != "passed":
            continue
        packages.update(environment_system_requirements(environment))
    return sorted(packages)


def process_next_environment_build():
    """Claim and process one queued environment build or dependency update."""
    environment = (
        Environment.query
        .filter_by(build_status="queued")
        .filter(Environment.is_builtin.is_(False))
        .order_by(Environment.updated_at.asc(), Environment.id.asc())
        .first()
    )
    if environment is None:
        return False
    if environment.is_managed:
        create_managed_environment(environment)
    else:
        update_registered_environment(environment)
    return True

def delete_managed_environment_files(environment):
    if not environment.is_managed:
        raise EnvironmentBuildError("Only Journeyman-managed environments can have their files removed.")
    root = managed_environment_root()
    path = Path(environment.path).resolve()
    if root not in path.parents or path == root:
        raise EnvironmentBuildError("Refusing to remove a path outside the managed environment root.")
    if path.exists():
        shutil.rmtree(path)


def validate_environment(environment):
    if is_application_environment(environment):
        root = Path(environment.path).expanduser()
        python_path = root / "bin" / "python"
        if not python_path.is_file() or not os.access(python_path, os.X_OK):
            environment.validation_status = "failed"
            environment.validation_message = (
                "Journeyman application Python was not found: {}".format(python_path)
            )
            environment.python_version = ""
            environment.ansible_version = ""
        else:
            try:
                environment.python_version = _run_version([str(python_path), "--version"])
                environment.ansible_version = ""
                environment.validation_status = "passed"
                environment.validation_message = "Journeyman application Python is available."
            except EnvironmentBuildError as exc:
                environment.validation_status = "failed"
                environment.validation_message = str(exc)
        environment.last_validated_at = datetime.now(timezone.utc)
        db.session.commit()
        return environment.validation_status == "passed"

    if environment.path == SYSTEM_ENVIRONMENT_PATH:
        ansible_path_value = shutil.which("ansible-playbook")
        python_path_value = shutil.which("python3") or shutil.which("python")
        errors = []
        if not ansible_path_value:
            errors.append("ansible-playbook was not found in the Journeyman service PATH.")
        if not python_path_value:
            errors.append("Python was not found in the Journeyman service PATH.")

        if errors:
            environment.validation_status = "failed"
            environment.validation_message = " ".join(errors)
            environment.python_version = ""
            environment.ansible_version = ""
        else:
            try:
                environment.python_version = _run_version([python_path_value, "--version"])
                environment.ansible_version = _run_version([ansible_path_value, "--version"])
                environment.validation_status = "passed"
                environment.validation_message = (
                    "System executables are available: {} and {}."
                    .format(python_path_value, ansible_path_value)
                )
            except EnvironmentBuildError as exc:
                environment.validation_status = "failed"
                environment.validation_message = str(exc)

        environment.last_validated_at = datetime.now(timezone.utc)
        db.session.commit()
        return environment.validation_status == "passed"

    root = Path(environment.path).expanduser()
    python_path = root / "bin" / "python"
    ansible_path = root / "bin" / "ansible-playbook"

    errors = []
    if not root.is_dir():
        errors.append(f"Directory does not exist: {root}")
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        errors.append(f"Executable Python was not found: {python_path}")
    if not ansible_path.is_file() or not os.access(ansible_path, os.X_OK):
        errors.append(f"Executable ansible-playbook was not found: {ansible_path}")

    if errors:
        environment.validation_status = "failed"
        environment.validation_message = " ".join(errors)
        environment.python_version = ""
        environment.ansible_version = ""
    else:
        try:
            environment.python_version = _run_version([str(python_path), "--version"])
            environment.ansible_version = _run_version([str(ansible_path), "--version"])
            environment.validation_status = "passed"
            environment.validation_message = "Python and ansible-playbook are available."
        except EnvironmentBuildError as exc:
            environment.validation_status = "failed"
            environment.validation_message = str(exc)

    environment.last_validated_at = datetime.now(timezone.utc)
    db.session.commit()
    return environment.validation_status == "passed"


def default_environment():
    ensure_builtin_environment()
    return Environment.query.filter_by(is_default=True, enabled=True).first()
