"""Seed and maintain Journeyman-owned administrative automation."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app import db
from app.models import (
    Credential,
    Inventory,
    Project,
    ProjectPackage,
    ProjectPackageInput,
    ProjectSchedule,
    ProjectStep,
    Repository,
)
from app.credential_types import CREDENTIAL_TYPE_MACHINE, CREDENTIAL_TYPE_URL
from app.models.project_package import (
    PACKAGE_ACCESS_RESTRICTED,
    PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
    PACKAGE_DISPLAY_NORMAL,
    PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    PACKAGE_INPUT_BOOLEAN,
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_INTEGER,
    PACKAGE_INPUT_TEXT,
    PACKAGE_BINDING_STEP_LIMIT,
)
from app.services.git import safe_repository_dir
from app.services.runners import CURRENT_REMOTE_RUNNER_VERSION


BUILTIN_OWNER = "__journeyman_builtin__"
BUILTIN_REPOSITORY_NAME = "ZZ - Journeyman Built-in Automation"
BUILTIN_INVENTORY_NAME = "ZZ - Journeyman Local Bootstrap"
REMOTE_RUNNER_PROJECT_NAME = "ZZ - Manage Remote Runner"
REMOTE_RUNNER_PACKAGE_NAME = "ZZ - Manage Remote Runner"
REMOTE_RUNNER_BUILTIN_KEY = "manage_remote_runner"
BACKUP_PROJECT_NAME = "ZZ - Backup Journeyman"
BACKUP_PACKAGE_NAME = "ZZ - Backup Journeyman"
BACKUP_BUILTIN_KEY = "backup_journeyman"
BACKUP_SCHEDULE_NAME = "ZZ - Daily Journeyman backup"
RELEASE_TEST_PROJECT_NAME = "ZZ - Journeyman Release Validation"
RELEASE_TEST_PACKAGE_NAME = "ZZ - Journeyman Release Validation"
RELEASE_TEST_BUILTIN_KEY = "release_validation"
RELEASE_TEST_FAILURE_PROJECT_NAME = "ZZ - Journeyman Release Failure Validation"
RELEASE_TEST_FAILURE_PACKAGE_NAME = "ZZ - Journeyman Release Failure Validation"
RELEASE_TEST_FAILURE_BUILTIN_KEY = "release_validation_failure"
BUILTIN_REPOSITORY_URL = "builtin://journeyman"


def is_builtin_project(project):
    return bool(project) and bool(project.builtin_key)


def is_builtin_package(package):
    return bool(package) and bool(package.builtin_key)


def _utcnow():
    return datetime.now(timezone.utc)


def _journeyman_server_url():
    fqdn = str(current_app.config.get("PUBLIC_FQDN") or "").strip()
    port = int(current_app.config.get("HTTPS_PORT") or 443)
    if not fqdn:
        raise RuntimeError("Journeyman PUBLIC_FQDN is not configured.")
    if port == 443:
        return "https://{}".format(fqdn)
    return "https://{}:{}".format(fqdn, port)


def _git_timeout_seconds():
    return max(
        1,
        int(
            current_app.config.get(
                "GIT_COMMAND_TIMEOUT_SECONDS",
                300,
            )
        ),
    )


def _run_git(args, cwd, env=None):
    timeout = _git_timeout_seconds()
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Built-in Git command exceeded its {} second timeout.".format(
                timeout
            )
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            "Unable to execute built-in Git command: {}".format(exc)
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _builtin_source_dir():
    return Path(__file__).resolve().parents[2] / "deploy" / "ansible"


def _materialise_builtin_repository(repository):
    checkout = Path(
        safe_repository_dir(
            current_app.config["REPOSITORY_ROOT"],
            repository.id,
        )
    )
    source_dir = _builtin_source_dir()

    release_test_dir = Path(__file__).resolve().parents[2] / "tests" / "playbooks"
    required = [
        source_dir / "manage-remote-runner.yml",
        source_dir / "install-remote-runner.yml",
        source_dir / "remove-remote-runner.yml",
        source_dir / "journeyman-backup-restore.yml",
        release_test_dir / "release_linux_validation.yml",
        release_test_dir / "linux_connectivity.yml",
        release_test_dir / "linux_become.yml",
        release_test_dir / "linux_runtime_variables.yml",
        release_test_dir / "release_linux_partial_failure.yml",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Built-in automation files are missing: {}".format(
                ", ".join(missing)
            )
        )

    checkout.mkdir(parents=True, exist_ok=True)
    for path in checkout.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    for source in required:
        shutil.copy2(source, checkout / source.name)

    if not (checkout / ".git").is_dir():
        _run_git(["init", "-b", "main"], checkout)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Journeyman",
            "GIT_AUTHOR_EMAIL": "journeyman@localhost",
            "GIT_COMMITTER_NAME": "Journeyman",
            "GIT_COMMITTER_EMAIL": "journeyman@localhost",
        }
    )
    _run_git(["add", "--all"], checkout, env=env)
    try:
        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(checkout),
            env=env,
            timeout=_git_timeout_seconds(),
            check=False,
        ).returncode != 0
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Built-in Git diff exceeded its configured timeout."
        ) from exc

    try:
        _run_git(["rev-parse", "--verify", "HEAD"], checkout, env=env)
        has_head = True
    except RuntimeError:
        has_head = False

    if changed or not has_head:
        _run_git(
            ["commit", "-m", "Journeyman built-in automation"],
            checkout,
            env=env,
        )

    commit = _run_git(["rev-parse", "HEAD"], checkout, env=env)
    repository.status = "up_to_date"
    repository.last_sync_at = _utcnow()
    repository.last_sync_message = "Journeyman built-in automation is current."
    repository.last_commit = commit
    repository.last_commit_message = "Journeyman built-in automation"
    repository.last_commit_author = "Journeyman"
    repository.last_commit_at = _utcnow()


def _ensure_input(package, position, **kwargs):
    package_input = next(
        (item for item in package.inputs if item.variable_name == kwargs["variable_name"]),
        None,
    )
    if package_input is None:
        package_input = ProjectPackageInput(
            position=position,
            variable_name=kwargs["variable_name"],
            label=kwargs["label"],
        )
        package.inputs.append(package_input)

    package_input.position = position
    package_input.label = kwargs["label"]
    package_input.help_text = kwargs.get("help_text", "")
    package_input.input_type = kwargs.get("input_type", PACKAGE_INPUT_TEXT)
    package_input.required = kwargs.get("required", False)
    package_input.is_secret = kwargs.get("is_secret", False)
    package_input.display_role = kwargs.get("display_role", PACKAGE_DISPLAY_NORMAL)
    package_input.binding_type = kwargs.get("binding_type", "extra_var")
    package_input.set_default_value(kwargs.get("default_value"))
    package_input.set_choices(kwargs.get("choices", []))
    package_input.set_validation(kwargs.get("validation", {}))
    package_input.set_conditions(kwargs.get("conditions", {}))
    return package_input


def ensure_builtin_admin_automation():
    """Create/update the built-in remote-runner Project and Package."""

    repository = Repository.query.filter_by(name=BUILTIN_REPOSITORY_NAME).first()
    if repository is None:
        repository = Repository(
            name=BUILTIN_REPOSITORY_NAME,
            description="Journeyman-managed built-in administrative playbooks.",
            repository_type="git",
            url=BUILTIN_REPOSITORY_URL,
            directory_path="",
            default_branch="main",
            status="never_synced",
        )
        db.session.add(repository)
        db.session.flush()
    else:
        repository.repository_type = "git"
        repository.url = BUILTIN_REPOSITORY_URL
        repository.directory_path = ""
        repository.default_branch = "main"

    _materialise_builtin_repository(repository)

    inventory = Inventory.query.filter_by(name=BUILTIN_INVENTORY_NAME).first()
    if inventory is None:
        inventory = Inventory(
            name=BUILTIN_INVENTORY_NAME,
            inventory_type="static",
            endpoint="",
            verify_tls=True,
            enabled=True,
            status="up_to_date",
        )
        db.session.add(inventory)
    inventory.config_json = json.dumps(
        {
            "content": (
                "all:\n"
                "  hosts:\n"
                "    localhost:\n"
                "      ansible_connection: local\n"
            )
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    inventory.status = "up_to_date"

    project = Project.query.filter_by(builtin_key=REMOTE_RUNNER_BUILTIN_KEY).first()
    if project is None:
        name_conflict = Project.query.filter_by(name=REMOTE_RUNNER_PROJECT_NAME).first()
        if name_conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Project because "{}" already exists.'
                .format(REMOTE_RUNNER_PROJECT_NAME)
            )
        project = Project(
            name=REMOTE_RUNNER_PROJECT_NAME,
            description=(
                "Built-in administrative Project used by the remote-runner "
                "management Package. Configure a suitable bootstrap credential "
                "on this Project before installing/removing runners."
            ),
            enabled=True,
            builtin_key=REMOTE_RUNNER_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            security_scope="private",
            execution_type="ansible",
            max_parallel_steps=1,
            runner_routing="local",
            default_runner_id=None,
        )
        db.session.add(project)
    project.name = REMOTE_RUNNER_PROJECT_NAME
    project.builtin_key = REMOTE_RUNNER_BUILTIN_KEY
    project.owner = BUILTIN_OWNER
    project.repository = repository
    project.inventory = inventory
    project.runner_routing = "local"
    project.runner_id = None
    project.default_runner_id = None
    project.enabled = True

    if len(project.steps) != 1:
        project.steps[:] = []
        project.steps.append(
            ProjectStep(
                position=1,
                name="Manage remote runner",
                repository=repository,
                playbook="manage-remote-runner.yml",
                enabled=True,
                refresh_repository=False,
            )
        )
    else:
        step = project.steps[0]
        step.position = 1
        step.name = "Manage remote runner"
        step.repository = repository
        step.playbook = "manage-remote-runner.yml"
        step.enabled = True
        step.refresh_repository = False

    package = ProjectPackage.query.filter_by(builtin_key=REMOTE_RUNNER_BUILTIN_KEY).first()
    if package is None:
        name_conflict = ProjectPackage.query.filter_by(name=REMOTE_RUNNER_PACKAGE_NAME).first()
        if name_conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Package because "{}" already exists.'
                .format(REMOTE_RUNNER_PACKAGE_NAME)
            )
        package = ProjectPackage(
            name=REMOTE_RUNNER_PACKAGE_NAME,
            description="Install, update, unregister, or delete a Journeyman remote runner.",
            project=project,
            enabled=True,
            builtin_key=REMOTE_RUNNER_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            access_mode=PACKAGE_ACCESS_RESTRICTED,
            warning_message=(
                "Administrative operation. Delete may revoke the runner and "
                "optionally remove Journeyman software and data from the target node."
            ),
            confirmation_required=True,
            confirmation_message="Confirm the requested remote-runner management action.",
        )
        db.session.add(package)
    package.name = REMOTE_RUNNER_PACKAGE_NAME
    package.builtin_key = REMOTE_RUNNER_BUILTIN_KEY
    package.owner = BUILTIN_OWNER
    package.project = project
    package.enabled = True
    package.access_mode = PACKAGE_ACCESS_RESTRICTED
    package.permissions[:] = []
    package.set_fixed_vars(
        {
            "journeyman_confirm_remove_all": True,
            "journeyman_server_url": _journeyman_server_url(),
            "journeyman_runner_expected_version": CURRENT_REMOTE_RUNNER_VERSION,
        }
    )

    desired_names = {
        "journeyman_manage_action",
        "journeyman_runner_host",
        "journeyman_runner_name",
        "journeyman_bootstrap_credential_id",
        "journeyman_runner_site",
        "journeyman_runner_max_concurrent_steps",
        "journeyman_pip_proxy_required",
        "journeyman_pip_proxy_credential_id",
        "journeyman_remove_runner_software",
    }
    for package_input in list(package.inputs):
        if package_input.variable_name not in desired_names:
            package.inputs.remove(package_input)
            db.session.delete(package_input)

    # Package input positions are unique within a package.  Built-in package
    # definitions can add/reorder inputs between Journeyman releases, so move
    # existing rows out of the final position range and flush before assigning
    # the new canonical positions below.  Without this two-phase resequence,
    # SQLite can attempt an INSERT/UPDATE into (package_id, position) before the
    # row currently occupying that position has moved, causing a transient
    # UNIQUE constraint failure while merely visiting a page that refreshes the
    # built-in automation definition.
    for temporary_position, package_input in enumerate(list(package.inputs), start=1):
        package_input.position = 1000000 + temporary_position
    db.session.flush()

    _ensure_input(
        package,
        1,
        variable_name="journeyman_manage_action",
        label="Action",
        input_type=PACKAGE_INPUT_CHOICE,
        required=True,
        default_value="install",
        choices=[
            {"value": "install", "label": "Install / register"},
            {"value": "update", "label": "Update runner software"},
            {"value": "unregister", "label": "Unregister (retain DB history)"},
            {"value": "delete", "label": "Delete runner registration"},
        ],
        display_role=PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
    )
    _ensure_input(
        package,
        2,
        variable_name="journeyman_runner_host",
        label="Target host",
        help_text=(
            "Resolvable hostname or FQDN used by Ansible/SSH to reach the physical "
            "runner host. Multiple logical runners may share this target during "
            "development testing."
        ),
        required=True,
        validation={"minimum_length": 1, "maximum_length": 255},
        display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    )
    _ensure_input(
        package,
        3,
        variable_name="journeyman_runner_name",
        label="Runner name",
        help_text=(
            "Logical Journeyman runner name. For same-host development runners, "
            "use a unique systemd-safe name such as dev-runner-1 while Target host "
            "remains the real SSH hostname."
        ),
        required=True,
        validation={
            "minimum_length": 1,
            "maximum_length": 120,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$",
        },
        display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    )
    bootstrap_credentials = (
        Credential.query
        .filter_by(credential_type=CREDENTIAL_TYPE_MACHINE)
        .order_by(Credential.name.asc(), Credential.id.asc())
        .all()
    )
    _ensure_input(
        package,
        4,
        variable_name="journeyman_bootstrap_credential_id",
        label="Bootstrap machine credential",
        help_text=(
            "Linux/UNIX Machine credential used by Ansible to SSH to the target "
            "host and perform privilege escalation during install/update. The "
            "designated bootstrap account must already exist on the Journeyman "
            "server and target runner, its public key must be present in the "
            "runner account's authorized_keys, and it must be permitted to use "
            "sudo non-interactively (typically NOPASSWD)."
        ),
        input_type=PACKAGE_INPUT_CHOICE,
        required=False,
        choices=[
            {"value": credential.id, "label": credential.name}
            for credential in bootstrap_credentials
        ],
        conditions={
            "visible_when": {"journeyman_manage_action": ["install", "update"]},
            "required_when": {"journeyman_manage_action": ["install", "update"]},
        },
    )
    _ensure_input(
        package,
        5,
        variable_name="journeyman_runner_site",
        label="Site / execution zone",
        help_text="Optional site used for runner grouping and future site-based routing.",
        required=False,
        validation={"maximum_length": 120},
        conditions={"visible_when": {"journeyman_manage_action": "install"}},
    )
    _ensure_input(
        package,
        6,
        variable_name="journeyman_runner_max_concurrent_steps",
        label="Maximum concurrent steps",
        help_text="Maximum work items Journeyman may assign to this runner at once.",
        input_type=PACKAGE_INPUT_INTEGER,
        required=False,
        default_value=1,
        validation={"minimum": 1, "maximum": 100},
        conditions={
            "visible_when": {"journeyman_manage_action": "install"},
            "required_when": {"journeyman_manage_action": "install"},
        },
    )
    _ensure_input(
        package,
        7,
        variable_name="journeyman_pip_proxy_required",
        label="Proxy required for pip",
        help_text="Enable when the target node must use an HTTP/HTTPS proxy to install Python packages.",
        input_type=PACKAGE_INPUT_BOOLEAN,
        required=False,
        default_value=False,
        conditions={"visible_when": {"journeyman_manage_action": ["install", "update"]}},
    )
    proxy_credentials = (
        Credential.query
        .filter_by(credential_type=CREDENTIAL_TYPE_URL)
        .order_by(Credential.name.asc(), Credential.id.asc())
        .all()
    )
    _ensure_input(
        package,
        8,
        variable_name="journeyman_pip_proxy_credential_id",
        label="pip proxy credential",
        help_text=(
            "URL / API credential used only while pip installs the runner Python "
            "dependencies. The credential must use None or HTTP Basic authentication."
        ),
        input_type=PACKAGE_INPUT_CHOICE,
        required=False,
        choices=[
            {"value": credential.id, "label": credential.name}
            for credential in proxy_credentials
        ],
        conditions={
            "visible_when": {
                "journeyman_manage_action": ["install", "update"],
                "journeyman_pip_proxy_required": True,
            },
            "required_when": {
                "journeyman_manage_action": ["install", "update"],
                "journeyman_pip_proxy_required": True,
            },
        },
    )
    _ensure_input(
        package,
        9,
        variable_name="journeyman_remove_runner_software",
        label="Remove Journeyman software and data from the node",
        help_text=(
            "Also remove the runner service and data. For a same-host development "
            "instance, only that instance's config/work/spool data is removed; "
            "shared /opt/journeyman runtime and service account are retained for "
            "sibling instances."
        ),
        input_type=PACKAGE_INPUT_BOOLEAN,
        required=False,
        default_value=False,
        display_role=PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
        conditions={
            "visible_when": {
                "journeyman_manage_action": ["unregister", "delete"],
            }
        },
    )

    backup_project = Project.query.filter_by(builtin_key=BACKUP_BUILTIN_KEY).first()
    if backup_project is None:
        name_conflict = Project.query.filter_by(name=BACKUP_PROJECT_NAME).first()
        if name_conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Project because "{}" already exists.'
                .format(BACKUP_PROJECT_NAME)
            )
        backup_project = Project(
            name=BACKUP_PROJECT_NAME,
            description=(
                "Built-in administrative Project that creates an online "
                "Journeyman backup. Configure a Machine credential whose account "
                "can SSH to this server and become root. Direct scheduled "
                "execution defaults to /tmp."
            ),
            enabled=True,
            builtin_key=BACKUP_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            security_scope="private",
            execution_type="ansible",
            max_parallel_steps=1,
            runner_routing="local",
            default_runner_id=None,
        )
        db.session.add(backup_project)

    backup_project.name = BACKUP_PROJECT_NAME
    backup_project.builtin_key = BACKUP_BUILTIN_KEY
    backup_project.owner = BUILTIN_OWNER
    backup_project.repository = repository
    backup_project.inventory = inventory
    backup_project.runner_routing = "local"
    backup_project.runner_id = None
    backup_project.default_runner_id = None
    backup_project.enabled = True

    if len(backup_project.steps) != 1:
        backup_project.steps[:] = []
        backup_project.steps.append(
            ProjectStep(
                position=1,
                name="Back up Journeyman",
                repository=repository,
                playbook="journeyman-backup-restore.yml",
                enabled=True,
                refresh_repository=False,
            )
        )
    else:
        backup_step = backup_project.steps[0]
        backup_step.position = 1
        backup_step.name = "Back up Journeyman"
        backup_step.repository = repository
        backup_step.playbook = "journeyman-backup-restore.yml"
        backup_step.enabled = True
        backup_step.refresh_repository = False

    backup_package = ProjectPackage.query.filter_by(builtin_key=BACKUP_BUILTIN_KEY).first()
    if backup_package is None:
        name_conflict = ProjectPackage.query.filter_by(name=BACKUP_PACKAGE_NAME).first()
        if name_conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Package because "{}" already exists.'
                .format(BACKUP_PACKAGE_NAME)
            )
        backup_package = ProjectPackage(
            name=BACKUP_PACKAGE_NAME,
            description="Create an online backup of this Journeyman server.",
            project=backup_project,
            enabled=True,
            builtin_key=BACKUP_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            access_mode=PACKAGE_ACCESS_RESTRICTED,
            warning_message=(
                "The resulting archive contains Journeyman secrets, credential "
                "encryption material, configuration, environments and database state."
            ),
            confirmation_required=False,
            confirmation_message="",
        )
        db.session.add(backup_package)

    backup_package.name = BACKUP_PACKAGE_NAME
    backup_package.builtin_key = BACKUP_BUILTIN_KEY
    backup_package.owner = BUILTIN_OWNER
    backup_package.project = backup_project
    backup_package.enabled = True
    backup_package.access_mode = PACKAGE_ACCESS_RESTRICTED
    backup_package.permissions[:] = []
    backup_package.set_fixed_vars({"var_action": "backup"})

    desired_backup_names = {"path"}
    for package_input in list(backup_package.inputs):
        if package_input.variable_name not in desired_backup_names:
            backup_package.inputs.remove(package_input)
            db.session.delete(package_input)

    for temporary_position, package_input in enumerate(
        list(backup_package.inputs), start=1
    ):
        package_input.position = 2000000 + temporary_position
    db.session.flush()

    _ensure_input(
        backup_package,
        1,
        variable_name="path",
        label="Backup destination directory",
        help_text=(
            "Existing directory on the Journeyman server. Existing files are not "
            "modified; a timestamped backup archive is created in this directory."
        ),
        input_type=PACKAGE_INPUT_TEXT,
        required=True,
        default_value="/tmp",
        validation={"minimum_length": 1, "maximum_length": 1000},
        display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    )

    db.session.flush()
    backup_schedule = ProjectSchedule.query.filter_by(
        project_id=backup_project.id,
        name=BACKUP_SCHEDULE_NAME,
    ).first()
    if backup_schedule is None:
        backup_schedule = ProjectSchedule(
            project=backup_project,
            name=BACKUP_SCHEDULE_NAME,
            schedule_type="daily",
            timezone_name="UTC",
            start_at=datetime(2020, 1, 1, 5, 0, tzinfo=timezone.utc),
            end_at=None,
            interval_minutes=None,
            weekdays="",
            enabled=False,
            next_run_at=None,
            created_by=BUILTIN_OWNER,
        )
        db.session.add(backup_schedule)

    db.session.commit()
    return {
        "repository": repository,
        "inventory": inventory,
        "project": project,
        "package": package,
        "backup_project": backup_project,
        "backup_package": backup_package,
        "backup_schedule": backup_schedule,
    }


def ensure_builtin_release_validation(settings):
    """Create/update the built-in Linux release-validation Project and Package."""
    if not settings or not settings.inventory or not settings.credential or not settings.host_pattern:
        return None

    repository = Repository.query.filter_by(name=BUILTIN_REPOSITORY_NAME).first()
    if repository is None:
        # The administrative seeder owns creation/materialisation of the shared
        # built-in repository and is intentionally idempotent.
        repository = ensure_builtin_admin_automation()["repository"]
    else:
        _materialise_builtin_repository(repository)

    project = Project.query.filter_by(builtin_key=RELEASE_TEST_BUILTIN_KEY).first()
    if project is None:
        conflict = Project.query.filter_by(name=RELEASE_TEST_PROJECT_NAME).first()
        if conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Project because "{}" already exists.'.format(
                    RELEASE_TEST_PROJECT_NAME
                )
            )
        project = Project(
            name=RELEASE_TEST_PROJECT_NAME,
            description=(
                "Built-in Linux operational validation used to verify an installed "
                "Journeyman release through its normal inventory, credential, runner "
                "and Ansible execution path."
            ),
            enabled=True,
            builtin_key=RELEASE_TEST_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            security_scope="private",
            execution_type="ansible",
            max_parallel_steps=1,
        )
        db.session.add(project)

    project.name = RELEASE_TEST_PROJECT_NAME
    project.builtin_key = RELEASE_TEST_BUILTIN_KEY
    project.owner = BUILTIN_OWNER
    project.repository = repository
    project.inventory = settings.inventory
    project.credentials[:] = [settings.credential]
    project.runner_id = None
    project.default_runner_id = None
    project.default_runner_crew = settings.runner_crew
    project.runner_routing = "remote_crew" if settings.runner_crew is not None else "local"
    project.enabled = True

    if len(project.steps) != 1:
        project.steps[:] = []
        project.steps.append(
            ProjectStep(
                position=1,
                name="Linux release validation",
                repository=repository,
                playbook="release_linux_validation.yml",
                enabled=True,
                refresh_repository=False,
            )
        )
    else:
        step = project.steps[0]
        step.position = 1
        step.name = "Linux release validation"
        step.repository = repository
        step.playbook = "release_linux_validation.yml"
        step.enabled = True
        step.refresh_repository = False

    package = ProjectPackage.query.filter_by(builtin_key=RELEASE_TEST_BUILTIN_KEY).first()
    if package is None:
        conflict = ProjectPackage.query.filter_by(name=RELEASE_TEST_PACKAGE_NAME).first()
        if conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Package because "{}" already exists.'.format(
                    RELEASE_TEST_PACKAGE_NAME
                )
            )
        package = ProjectPackage(
            name=RELEASE_TEST_PACKAGE_NAME,
            description=(
                "Run the built-in Linux release-validation playbooks against the "
                "configured non-production test target."
            ),
            project=project,
            enabled=True,
            builtin_key=RELEASE_TEST_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            access_mode=PACKAGE_ACCESS_RESTRICTED,
            warning_message=(
                "Release validation performs SSH login and configured sudo/become "
                "operations on the selected test hosts."
            ),
            confirmation_required=True,
            confirmation_message="Confirm execution against the selected release-test hosts.",
        )
        db.session.add(package)

    package.name = RELEASE_TEST_PACKAGE_NAME
    package.builtin_key = RELEASE_TEST_BUILTIN_KEY
    package.owner = BUILTIN_OWNER
    package.project = project
    package.enabled = True
    package.access_mode = PACKAGE_ACCESS_RESTRICTED
    package.permissions[:] = []
    credential_data = settings.credential.get_credential_data()
    package.set_fixed_vars(
        {
            "journeyman_release_test_expected_login_user": settings.credential.username,
            "journeyman_release_test_expected_become_user": str(
                credential_data.get("become_user") or "root"
            ),
            "journeyman_release_test_become_users": settings.become_users(),
            "journeyman_release_test_token": "journeyman-release-validation",
        }
    )

    desired_names = {"journeyman_release_test_hosts"}
    for package_input in list(package.inputs):
        if package_input.variable_name not in desired_names:
            package.inputs.remove(package_input)
            db.session.delete(package_input)
    for temporary_position, package_input in enumerate(list(package.inputs), start=1):
        package_input.position = 3000000 + temporary_position
    db.session.flush()

    _ensure_input(
        package,
        1,
        variable_name="journeyman_release_test_hosts",
        label="Test hosts",
        help_text=(
            "Ansible host pattern used as the Step limit. Review this before each "
            "release-validation run."
        ),
        required=True,
        default_value=settings.host_pattern,
        validation={"minimum_length": 1, "maximum_length": 500},
        display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET,
        binding_type=PACKAGE_BINDING_STEP_LIMIT,
    )

    failure_project = Project.query.filter_by(
        builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY
    ).first()
    if failure_project is None:
        conflict = Project.query.filter_by(name=RELEASE_TEST_FAILURE_PROJECT_NAME).first()
        if conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Project because "{}" already exists.'.format(
                    RELEASE_TEST_FAILURE_PROJECT_NAME
                )
            )
        failure_project = Project(
            name=RELEASE_TEST_FAILURE_PROJECT_NAME,
            description=(
                "Built-in expected-failure validation used to verify multi-host "
                "slice failure propagation through Job, Step and Slice state."
            ),
            enabled=True,
            builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            security_scope="private",
            execution_type="ansible",
            max_parallel_steps=1,
        )
        db.session.add(failure_project)

    failure_project.name = RELEASE_TEST_FAILURE_PROJECT_NAME
    failure_project.builtin_key = RELEASE_TEST_FAILURE_BUILTIN_KEY
    failure_project.owner = BUILTIN_OWNER
    failure_project.repository = repository
    failure_project.inventory = settings.inventory
    failure_project.credentials[:] = [settings.credential]
    failure_project.runner_id = None
    failure_project.default_runner_id = None
    failure_project.default_runner_crew = settings.runner_crew
    failure_project.runner_routing = (
        "remote_crew" if settings.runner_crew is not None else "local"
    )
    failure_project.enabled = True

    if len(failure_project.steps) != 1:
        failure_project.steps[:] = []
        failure_project.steps.append(
            ProjectStep(
                position=1,
                name="Expected partial failure",
                repository=repository,
                playbook="release_linux_partial_failure.yml",
                enabled=True,
                refresh_repository=False,
            )
        )
    else:
        failure_step = failure_project.steps[0]
        failure_step.position = 1
        failure_step.name = "Expected partial failure"
        failure_step.repository = repository
        failure_step.playbook = "release_linux_partial_failure.yml"
        failure_step.enabled = True
        failure_step.refresh_repository = False

    failure_package = ProjectPackage.query.filter_by(
        builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY
    ).first()
    if failure_package is None:
        conflict = ProjectPackage.query.filter_by(
            name=RELEASE_TEST_FAILURE_PACKAGE_NAME
        ).first()
        if conflict is not None:
            raise RuntimeError(
                'Cannot seed built-in Package because "{}" already exists.'.format(
                    RELEASE_TEST_FAILURE_PACKAGE_NAME
                )
            )
        failure_package = ProjectPackage(
            name=RELEASE_TEST_FAILURE_PACKAGE_NAME,
            description=(
                "Deliberately fail one selected Linux host while the remaining "
                "targets continue, then verify Journeyman failure propagation."
            ),
            project=failure_project,
            enabled=True,
            builtin_key=RELEASE_TEST_FAILURE_BUILTIN_KEY,
            owner=BUILTIN_OWNER,
            access_mode=PACKAGE_ACCESS_RESTRICTED,
            warning_message=(
                "This validation is expected to create a FAILED Job. It deliberately "
                "fails one selected test host without changing target state."
            ),
            confirmation_required=True,
            confirmation_message=(
                "Confirm deliberate expected failure against the selected release-test hosts."
            ),
        )
        db.session.add(failure_package)

    failure_package.name = RELEASE_TEST_FAILURE_PACKAGE_NAME
    failure_package.builtin_key = RELEASE_TEST_FAILURE_BUILTIN_KEY
    failure_package.owner = BUILTIN_OWNER
    failure_package.project = failure_project
    failure_package.enabled = True
    failure_package.access_mode = PACKAGE_ACCESS_RESTRICTED
    failure_package.permissions[:] = []
    failure_package.set_fixed_vars(
        {
            "journeyman_release_test_expected_login_user": settings.credential.username,
        }
    )

    desired_failure_names = {
        "journeyman_release_test_hosts",
        "journeyman_release_test_failure_host",
    }
    for package_input in list(failure_package.inputs):
        if package_input.variable_name not in desired_failure_names:
            failure_package.inputs.remove(package_input)
            db.session.delete(package_input)
    for temporary_position, package_input in enumerate(
        list(failure_package.inputs), start=1
    ):
        package_input.position = 3000000 + temporary_position
    db.session.flush()

    _ensure_input(
        failure_package,
        1,
        variable_name="journeyman_release_test_hosts",
        label="Test hosts",
        help_text=(
            "Ansible host pattern used as the Step limit. Use at least two hosts "
            "to exercise a multi-host execution slice."
        ),
        required=True,
        default_value=settings.host_pattern,
        validation={"minimum_length": 1, "maximum_length": 500},
        display_role=PACKAGE_DISPLAY_OPERATIONAL_TARGET,
        binding_type=PACKAGE_BINDING_STEP_LIMIT,
    )
    _ensure_input(
        failure_package,
        2,
        variable_name="journeyman_release_test_failure_host",
        label="Host to fail deliberately",
        help_text=(
            "Exact Ansible inventory_hostname of one target host. The playbook "
            "fails only this host; the other selected hosts must continue."
        ),
        required=True,
        default_value="",
        validation={"minimum_length": 1, "maximum_length": 255},
        display_role=PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
    )

    db.session.commit()
    return {
        "repository": repository,
        "project": project,
        "package": package,
        "failure_project": failure_project,
        "failure_package": failure_package,
    }
