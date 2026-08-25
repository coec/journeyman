import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from flask import g, request
from flask_migrate import upgrade

from app import create_app, db
from app.config import Config
import app.auth as auth
import app.routes as routes
from app.models import (
    Job,
    JobPackageSnapshot,
    Project,
    ProjectPackage,
    ProjectPackageInput,
    ProjectPackagePermission,
)
from app.models.project_package import (
    PACKAGE_ACCESS_AUTHENTICATED,
    PACKAGE_ACCESS_RESTRICTED,
    PACKAGE_BINDING_EXTRA_VAR,
    PACKAGE_DISPLAY_NORMAL,
    PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    PACKAGE_INPUT_PASSWORD,
    PACKAGE_INPUT_TEXT,
    PACKAGE_PRINCIPAL_GROUP,
    PACKAGE_PRINCIPAL_USER,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)


def _create_package(
    *,
    name,
    project,
    access_mode,
    enabled=True,
):
    package = ProjectPackage(
        name=name,
        description=(
            "{} description".format(name)
        ),
        project=project,
        enabled=enabled,
        owner="package.owner",
        access_mode=access_mode,
        warning_message="",
        confirmation_required=True,
        confirmation_message=(
            "Confirm the Package launch."
        ),
    )

    package.set_fixed_vars(
        {
            "locked_value": "locked",
        }
    )

    return package


def _add_permission(
    package,
    principal_type,
    principal_name,
):
    package.permissions.append(
        ProjectPackagePermission(
            principal_type=principal_type,
            principal_name=principal_name,
        )
    )


def _add_text_input(
    package,
    *,
    position,
    variable_name,
    label,
    required=True,
    display_role=PACKAGE_DISPLAY_NORMAL,
):
    package_input = ProjectPackageInput(
        position=position,
        variable_name=variable_name,
        label=label,
        help_text="Route test input.",
        input_type=PACKAGE_INPUT_TEXT,
        required=required,
        is_secret=False,
        display_role=display_role,
        binding_type=PACKAGE_BINDING_EXTRA_VAR,
    )

    package_input.set_default_value(
        None
    )

    package_input.set_choices(
        []
    )

    package_input.set_validation(
        {
            "minimum_length": 1,
            "maximum_length": 120,
        }
    )

    package_input.set_conditions(
        {}
    )

    package.inputs.append(
        package_input
    )

    return package_input


def _add_password_input(
    package,
    *,
    position,
    variable_name,
    label,
):
    package_input = ProjectPackageInput(
        position=position,
        variable_name=variable_name,
        label=label,
        help_text="Secret route test input.",
        input_type=PACKAGE_INPUT_PASSWORD,
        required=True,
        is_secret=True,
        display_role=PACKAGE_DISPLAY_NORMAL,
        binding_type=PACKAGE_BINDING_EXTRA_VAR,
    )

    package_input.set_default_value(
        None
    )

    package_input.set_choices(
        []
    )

    package_input.set_validation(
        {
            "minimum_length": 1,
            "maximum_length": 200,
        }
    )

    package_input.set_conditions(
        {}
    )

    package.inputs.append(
        package_input
    )

    return package_input


@pytest.fixture
def app(
    tmp_path,
    monkeypatch,
):
    repository_root = (
        tmp_path / "repositories"
    )

    log_root = (
        tmp_path / "logs"
    )

    repository_root.mkdir(
        mode=0o700
    )

    log_root.mkdir(
        mode=0o700
    )

    database_path = (
        tmp_path / "route-tests.db"
    )

    credential_key_path = (
        tmp_path / "credential.key"
    )

    credential_key_path.write_bytes(
        Fernet.generate_key() + b"\n"
    )

    os.chmod(
        credential_key_path,
        0o600,
    )

    monkeypatch.setenv(
        "JOURNEYMAN_CREDENTIAL_KEY_FILE",
        str(credential_key_path),
    )

    test_config = type(
        "RouteTestConfig",
        (Config,),
        {
            "TESTING": True,
            "AUTHENTICATION_DISABLED": True,
            "FALLBACK_ADMIN_USERNAME": "admin",
            "FALLBACK_ADMIN_PASSWORD_HASH_FILE": str(
                tmp_path / "fallback-admin-password.hash"
            ),
            # Existing route tests exercise Package behaviour and
            # authorisation. CSRF has a separate dedicated suite.
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": (
                "journeyman-route-test-key"
            ),
            # Match the production session policy.  Flask otherwise
            # falls back to its 31-day default for this synthetic
            # test configuration.
            "PERMANENT_SESSION_LIFETIME": 28800,
            "SESSION_REFRESH_EACH_REQUEST": True,
            "SQLALCHEMY_DATABASE_URI": (
                "sqlite:///{}".format(
                    database_path
                )
            ),
            "SQLALCHEMY_TRACK_MODIFICATIONS": (
                False
            ),
            "REPOSITORY_ROOT": repository_root,
            "LOG_ROOT": log_root,
            "MANAGED_ENVIRONMENT_ROOT": (
                tmp_path / "environments"
            ),

            # System Settings test defaults. Tests use an isolated
            # temporary TLS directory and never read live host config.
            "PUBLIC_FQDN": (
                "journeyman.example.com"
            ),
            "TLS_ROOT": (
                tmp_path / "tls"
            ),
            "TLS_CERTIFICATE_PATH": str(
                tmp_path
                / "tls"
                / "journeyman-cert.pem"
            ),
            "TLS_PRIVATE_KEY_PATH": str(
                tmp_path
                / "tls"
                / "journeyman-key.pem"
            ),
            "TLS_CHAIN_PATH": "",
            "HTTPS_PORT": 443,
            "JOB_TIMEOUT_SECONDS": 30,
            "GIT_COMMAND_TIMEOUT_SECONDS": 5,
            "PACKAGE_LAUNCH_PREVIEW_TTL_SECONDS": (
                900
            ),
        },
    )

    monkeypatch.setattr(
        auth,
        "DEVELOPMENT_ADMINS",
        {
            "admin",
        },
    )

    instance_path = tmp_path / "instance"
    instance_path.mkdir(mode=0o700)

    journeyman_app = create_app(
        test_config,
        instance_path=instance_path,
    )

    @journeyman_app.before_request
    def load_route_test_identity():
        """
        Override the temporary hard-coded identity for route tests.

        These headers are only interpreted by this isolated test app.
        When a test enables real authentication, leave the identity
        established by Journeyman's normal session/login hooks alone.
        """

        if not journeyman_app.config.get(
            "AUTHENTICATION_DISABLED",
            False,
        ):
            return None

        username = str(
            request.headers.get(
                "X-Test-Username",
                "outsider",
            )
            or ""
        ).strip()

        raw_groups = str(
            request.headers.get(
                "X-Test-Groups",
                "",
            )
            or ""
        )

        group_names = frozenset(
            group_name.strip()
            for group_name
            in raw_groups.split(",")
            if group_name.strip()
        )

        g.authenticated_username = username
        g.authenticated_role = (
            "Administrator"
            if username in auth.DEVELOPMENT_ADMINS
            else "User"
        )
        g.authenticated_display_name = username
        g.authenticated_group_names = (
            group_names
        )
        g.authenticated_user_object_guid = None
        g.authenticated_group_object_guids = frozenset()
        g.authenticated_via = "test"

    journeyman_app.test_preview_state = {
        "digest": "preview-digest-v1",
        "inventory_type": "static",
        "refresh_inventory_after": False,
        "refresh_affects_filtered_targets": False,
        "target_hosts_may_change": False,
    }

    journeyman_app.test_preview_calls = []
    journeyman_app.test_queue_calls = []

    def fake_build_project_execution_preview(
        project,
        step_limit_override=None,
        refresh_repositories=False,
        refresh_inventory_sources=False,
        inventory_bindings=None,
        progress=None,
    ):
        effective_limit = str(
            step_limit_override or ""
        ).strip()

        journeyman_app.test_preview_calls.append(
            {
                "project_id": project.id,
                "step_limit_override": (
                    effective_limit
                ),
                "refresh_repositories": bool(
                    refresh_repositories
                ),
                "refresh_inventory_sources": bool(
                    refresh_inventory_sources
                ),
                "inventory_bindings": dict(
                    inventory_bindings or {}
                ),
            }
        )

        preview_step = SimpleNamespace(
            position=1,
            name="Route Test Step",
            playbook="route_test.yml",
            repository_name="Test Repository",
            repository_commit=(
                "0123456789abcdef"
            ),
            inventory_name="Test Inventory",
            inventory_type=(
                journeyman_app.test_preview_state[
                    "inventory_type"
                ]
            ),
            inventory_is_override=False,
            refresh_inventory_after=(
                journeyman_app.test_preview_state[
                    "refresh_inventory_after"
                ]
            ),
            refresh_affects_filtered_targets=(
                journeyman_app.test_preview_state[
                    "refresh_affects_filtered_targets"
                ]
            ),
            target_hosts_may_change=(
                journeyman_app.test_preview_state[
                    "target_hosts_may_change"
                ]
            ),
            limit=effective_limit,
            target_count=1,
            target_hosts=(
                "execution-host.example",
            ),
        )

        return SimpleNamespace(
            digest=(
                journeyman_app
                .test_preview_state[
                    "digest"
                ]
            ),
            steps=[
                preview_step,
            ],
            total_unique_hosts=1,
            has_large_target=False,
            has_zero_target=False,
            has_dynamic_targets=(
                journeyman_app.test_preview_state[
                    "target_hosts_may_change"
                ]
            ),
            large_target_threshold=100,
            resolved_inventory_data={
                1: {
                    "all": {
                        "hosts": {
                            "execution-host.example": {}
                        }
                    }
                }
            },
        )

    def fake_queue_project_execution(
        *,
        project,
        requested_by,
        message,
        resolved_inventory_data,
        package_execution,
        progress=None,
    ):
        assert package_execution is not None

        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by=requested_by,
            message=message,
        )

        package_snapshot = (
            JobPackageSnapshot(
                package_id=(
                    package_execution.package_id
                ),
                package_name=(
                    package_execution.package_name
                ),
                package_owner=(
                    package_execution.package_owner
                ),
                step_limit=(
                    package_execution.step_limit
                ),
            )
        )

        package_snapshot.set_package_definition(
            package_execution.definition
        )

        package_snapshot.set_display_values(
            package_execution.display_values
        )

        package_snapshot.set_operational_targets(
            package_execution
            .operational_targets
        )

        package_snapshot.set_execution_vars(
            package_execution.execution_vars
        )

        job.package_snapshot = (
            package_snapshot
        )

        db.session.add(
            job
        )

        db.session.commit()

        journeyman_app.test_queue_calls.append(
            {
                "job_id": job.id,
                "project_id": project.id,
                "requested_by": requested_by,
                "message": message,
                "resolved_inventory_data": (
                    resolved_inventory_data
                ),
                "package_execution": (
                    package_execution
                ),
            }
        )

        return job

    monkeypatch.setattr(
        routes,
        "build_project_execution_preview",
        fake_build_project_execution_preview,
    )

    monkeypatch.setattr(
        routes,
        "queue_project_execution",
        fake_queue_project_execution,
    )

    with journeyman_app.app_context():
        upgrade(
            directory=str(
                PROJECT_ROOT / "migrations"
            )
        )

    yield journeyman_app

    with journeyman_app.app_context():
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_packages(app):
    with app.app_context():
        enabled_project = Project(
            name="Enabled Route Project",
            description="",
            enabled=True,
            owner="project.owner",
            security_scope="private",
        )

        disabled_project = Project(
            name="Disabled Route Project",
            description="",
            enabled=False,
            owner="project.owner",
            security_scope="private",
        )

        user_package = _create_package(
            name="User Restricted Package",
            project=enabled_project,
            access_mode=(
                PACKAGE_ACCESS_RESTRICTED
            ),
        )

        _add_permission(
            user_package,
            PACKAGE_PRINCIPAL_USER,
            "alice",
        )

        user_device_input = _add_text_input(
            user_package,
            position=1,
            variable_name="device",
            label="Device",
            display_role=(
                PACKAGE_DISPLAY_OPERATIONAL_TARGET
            ),
        )

        user_password_input = (
            _add_password_input(
                user_package,
                position=2,
                variable_name="password",
                label="Password",
            )
        )

        group_package = _create_package(
            name="Group Restricted Package",
            project=enabled_project,
            access_mode=(
                PACKAGE_ACCESS_RESTRICTED
            ),
        )

        _add_permission(
            group_package,
            PACKAGE_PRINCIPAL_GROUP,
            "Network Operators",
        )

        authenticated_package = (
            _create_package(
                name="Authenticated Package",
                project=enabled_project,
                access_mode=(
                    PACKAGE_ACCESS_AUTHENTICATED
                ),
            )
        )

        disabled_package = _create_package(
            name="Disabled Package",
            project=enabled_project,
            access_mode=(
                PACKAGE_ACCESS_AUTHENTICATED
            ),
            enabled=False,
        )

        disabled_project_package = (
            _create_package(
                name="Disabled Project Package",
                project=disabled_project,
                access_mode=(
                    PACKAGE_ACCESS_AUTHENTICATED
                ),
            )
        )

        db.session.add_all(
            [
                enabled_project,
                disabled_project,
                user_package,
                group_package,
                authenticated_package,
                disabled_package,
                disabled_project_package,
            ]
        )

        db.session.commit()

        result = {
            "enabled_project": (
                enabled_project.id
            ),
            "disabled_project": (
                disabled_project.id
            ),
            "user_package": (
                user_package.id
            ),
            "user_device_input": (
                user_device_input.id
            ),
            "user_password_input": (
                user_password_input.id
            ),
            "group_package": (
                group_package.id
            ),
            "authenticated_package": (
                authenticated_package.id
            ),
            "disabled_package": (
                disabled_package.id
            ),
            "disabled_project_package": (
                disabled_project_package.id
            ),
        }

    return result
