import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.security
from cryptography.fernet import Fernet
from flask import Flask, g
from werkzeug.datastructures import MultiDict

from app.auth import (
    can_launch_package,
    can_view_job,
)
from app.models import (
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
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
    PACKAGE_INPUT_PASSWORD,
    PACKAGE_INPUT_TEXT,
    PACKAGE_PRINCIPAL_GROUP,
    PACKAGE_PRINCIPAL_USER,
)
from app.services.project_package_launch import (
    PackageLaunchTokenError,
    create_package_launch_token,
    package_definition_digest,
    prepare_package_launch,
    read_package_launch_token,
)


UNSET = object()


@pytest.fixture
def flask_app():
    app = Flask(__name__)

    app.config.update(
        TESTING=True,
        SECRET_KEY="journeyman-test-key",
        PACKAGE_LAUNCH_PREVIEW_TTL_SECONDS=900,
    )

    return app


@pytest.fixture
def credential_key(monkeypatch, tmp_path):
    key_path = (
        tmp_path / "credential.key"
    )

    key_path.write_bytes(
        Fernet.generate_key() + b"\n"
    )

    os.chmod(
        key_path,
        0o600,
    )

    monkeypatch.setenv(
        "JOURNEYMAN_CREDENTIAL_KEY_FILE",
        str(key_path),
    )

    return key_path


def make_package(
    *,
    package_id=100,
    access_mode=PACKAGE_ACCESS_RESTRICTED,
    package_enabled=True,
    project_enabled=True,
):
    project = Project(
        id=200,
        name="Security Test Project",
        description="",
        enabled=project_enabled,
        owner="owner",
        security_scope="private",
    )

    package = ProjectPackage(
        id=package_id,
        name="Security Test Package",
        description="",
        project=project,
        enabled=package_enabled,
        owner="owner",
        access_mode=access_mode,
        warning_message="",
        confirmation_required=True,
        confirmation_message="",
    )

    package.set_fixed_vars(
        {
            "locked_value": "locked",
        }
    )

    return package


def add_permission(
    package,
    principal_type,
    principal_name,
):
    permission = ProjectPackagePermission(
        principal_type=principal_type,
        principal_name=principal_name,
    )

    package.permissions.append(
        permission
    )

    return permission


def add_input(
    package,
    *,
    input_id,
    position,
    variable_name,
    label,
    input_type=PACKAGE_INPUT_TEXT,
    required=False,
    is_secret=False,
    default=UNSET,
    choices=None,
    validation=None,
    conditions=None,
    display_role=PACKAGE_DISPLAY_NORMAL,
    binding_type=PACKAGE_BINDING_EXTRA_VAR,
):
    package_input = ProjectPackageInput(
        id=input_id,
        position=position,
        variable_name=variable_name,
        label=label,
        help_text="",
        input_type=input_type,
        required=required,
        is_secret=is_secret,
        display_role=display_role,
        binding_type=binding_type,
    )

    if default is not UNSET:
        package_input.set_default_value(
            default
        )

    package_input.set_choices(
        choices or []
    )

    package_input.set_validation(
        validation or {}
    )

    package_input.set_conditions(
        conditions or {}
    )

    package.inputs.append(
        package_input
    )

    return package_input


def form_field(package_input):
    return "package_value_{}".format(
        package_input.id
    )


def prepare_successfully(
    package,
    form_values,
):
    errors, fields, prepared = (
        prepare_package_launch(
            package=package,
            form=MultiDict(form_values),
        )
    )

    assert errors == []
    assert prepared is not None

    return (
        fields,
        prepared,
    )


def test_package_launch_permission_matrix(flask_app):
    with flask_app.app_context():
        package = make_package()

        add_permission(
            package,
            PACKAGE_PRINCIPAL_USER,
            "Alice",
        )

        add_permission(
            package,
            PACKAGE_PRINCIPAL_GROUP,
            "Network Operators",
        )

        assert can_launch_package(
            package,
            username="alice",
            group_names=(),
            is_admin=False,
        )

        assert can_launch_package(
            package,
            username="someone.else",
            group_names={
                "network operators",
            },
            is_admin=False,
        )

        assert not can_launch_package(
            package,
            username="someone.else",
            group_names=(),
            is_admin=False,
        )

        assert can_launch_package(
            package,
            username="administrator",
            group_names=(),
            is_admin=True,
        )

        authenticated_package = make_package(
            package_id=101,
            access_mode=(
                PACKAGE_ACCESS_AUTHENTICATED
            ),
        )

        assert can_launch_package(
            authenticated_package,
            username="any.user",
            group_names=(),
            is_admin=False,
        )

def test_disabled_package_or_project_cannot_launch():
    disabled_package = make_package(
        package_enabled=False,
    )

    assert not can_launch_package(
        disabled_package,
        username="administrator",
        group_names=(),
        is_admin=True,
    )

    disabled_project_package = make_package(
        package_id=102,
        project_enabled=False,
    )

    assert not can_launch_package(
        disabled_project_package,
        username="administrator",
        group_names=(),
        is_admin=True,
    )


def test_cross_user_job_visibility_is_denied(
    flask_app,
):
    job = SimpleNamespace(
        requested_by="alice"
    )

    with flask_app.test_request_context():
        g.authenticated_username = "alice"
        g.authenticated_group_names = (
            frozenset()
        )

        assert can_view_job(job)

        g.authenticated_username = "bob"

        assert not can_view_job(job)


def test_hidden_conditional_value_is_ignored():
    package = make_package()

    action = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="action",
        label="Action",
        input_type=PACKAGE_INPUT_CHOICE,
        required=True,
        choices=[
            {
                "value": "skip",
                "label": "Skip",
            },
            {
                "value": "show",
                "label": "Show",
            },
        ],
    )

    hidden_secret = add_input(
        package,
        input_id=2,
        position=2,
        variable_name="hidden_secret",
        label="Hidden secret",
        input_type=PACKAGE_INPUT_PASSWORD,
        required=False,
        is_secret=True,
        conditions={
            "visible_when": {
                "action": "show",
            },
            "required_when": {
                "action": "show",
            },
        },
    )

    secret_value = (
        "SHOULD-NOT-BE-ACCEPTED"
    )

    fields, prepared = prepare_successfully(
        package,
        {
            form_field(action): json.dumps(
                "skip"
            ),
            form_field(hidden_secret): (
                secret_value
            ),
        },
    )

    execution_vars = (
        prepared.execution_data.execution_vars
    )

    assert execution_vars[
        "locked_value"
    ] == "locked"

    assert execution_vars[
        "action"
    ] == "skip"

    assert (
        "hidden_secret"
        not in execution_vars
    )

    assert secret_value not in repr(
        fields
    )


def test_conditionally_required_value_is_enforced():
    package = make_package()

    action = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="action",
        label="Action",
        input_type=PACKAGE_INPUT_CHOICE,
        required=True,
        choices=[
            {
                "value": "skip",
                "label": "Skip",
            },
            {
                "value": "show",
                "label": "Show",
            },
        ],
    )

    add_input(
        package,
        input_id=2,
        position=2,
        variable_name="detail",
        label="Detail",
        required=False,
        conditions={
            "visible_when": {
                "action": "show",
            },
            "required_when": {
                "action": "show",
            },
        },
    )

    errors, fields, prepared = (
        prepare_package_launch(
            package=package,
            form=MultiDict(
                {
                    form_field(action):
                        json.dumps("show"),
                }
            ),
        )
    )

    assert prepared is None

    assert any(
        "Detail is required."
        in error
        for error in errors
    )

    detail_field = next(
        field
        for field in fields
        if field["variable_name"]
        == "detail"
    )

    assert detail_field["visible"]
    assert detail_field["required"]


def test_arbitrary_form_values_cannot_override_locked_vars():
    package = make_package()

    normal_input = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="normal_value",
        label="Normal value",
        required=True,
    )

    _, prepared = prepare_successfully(
        package,
        {
            form_field(normal_input):
                "accepted",
            "locked_value":
                "browser-attempted-override",
            "extra_vars":
                '{"locked_value":"hacked"}',
        },
    )

    execution_vars = (
        prepared.execution_data.execution_vars
    )

    assert execution_vars == {
        "locked_value": "locked",
        "normal_value": "accepted",
    }


def test_secret_value_is_encrypted_and_not_displayed(
    credential_key,
):
    package = make_package()

    password_input = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="password",
        label="Password",
        input_type=PACKAGE_INPUT_PASSWORD,
        required=True,
        is_secret=True,
    )

    secret_value = (
        "unique-secret-value-839241"
    )

    fields, prepared = prepare_successfully(
        package,
        {
            form_field(password_input):
                secret_value,
        },
    )

    execution_data = (
        prepared.execution_data
    )

    assert (
        execution_data.execution_vars[
            "password"
        ]
        == secret_value
    )

    assert execution_data.display_values == []
    assert (
        execution_data.operational_targets
        == []
    )

    assert secret_value not in repr(
        fields
    )

    snapshot = JobPackageSnapshot(
        package_name=package.name,
        package_owner=package.owner,
        step_limit="",
    )

    snapshot.set_package_definition(
        execution_data.definition
    )

    snapshot.set_display_values(
        execution_data.display_values
    )

    snapshot.set_operational_targets(
        execution_data.operational_targets
    )

    snapshot.set_execution_vars(
        execution_data.execution_vars
    )

    assert (
        secret_value.encode("utf-8")
        not in snapshot.encrypted_extra_vars
    )

    assert (
        secret_value
        not in snapshot.package_definition_json
    )

    assert (
        secret_value
        not in snapshot.display_values_json
    )

    assert (
        secret_value
        not in snapshot.operational_targets_json
    )

    assert (
        snapshot.get_execution_vars()[
            "password"
        ]
        == secret_value
    )


def test_operational_target_is_snapshotted():
    package = make_package()

    device_input = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="device",
        label="Device",
        required=True,
        display_role=(
            PACKAGE_DISPLAY_OPERATIONAL_TARGET
        ),
    )

    _, prepared = prepare_successfully(
        package,
        {
            form_field(device_input):
                "switch01",
        },
    )

    execution_data = (
        prepared.execution_data
    )

    assert (
        execution_data.operational_targets
        == ["switch01"]
    )

    assert execution_data.display_values == [
        {
            "variable_name": "device",
            "label": "Device",
            "value": "switch01",
            "display_value": "switch01",
            "display_role": (
                PACKAGE_DISPLAY_OPERATIONAL_TARGET
            ),
            "binding_type": (
                PACKAGE_BINDING_EXTRA_VAR
            ),
            "is_secret": False,
        }
    ]


def test_launch_token_is_bound_to_user_and_package(
    flask_app,
    credential_key,
):
    package = make_package()

    device_input = add_input(
        package,
        input_id=1,
        position=1,
        variable_name="device",
        label="Device",
        required=True,
    )

    _, prepared = prepare_successfully(
        package,
        {
            form_field(device_input):
                "switch01",
        },
    )

    with flask_app.app_context():
        token = create_package_launch_token(
            execution_data=(
                prepared.execution_data
            ),
            requested_by="alice",
            preview_digest="preview-digest",
        )

        payload = read_package_launch_token(
            token,
            expected_package_id=package.id,
            expected_username="alice",
        )

        assert (
            payload["preview_digest"]
            == "preview-digest"
        )

        assert (
            payload["execution_vars"][
                "device"
            ]
            == "switch01"
        )

        with pytest.raises(
            PackageLaunchTokenError
        ):
            read_package_launch_token(
                token,
                expected_package_id=999,
                expected_username="alice",
            )

        with pytest.raises(
            PackageLaunchTokenError
        ):
            read_package_launch_token(
                token,
                expected_package_id=(
                    package.id
                ),
                expected_username="bob",
            )


def test_tampered_launch_token_is_rejected(
    flask_app,
    credential_key,
):
    package = make_package()

    _, prepared = prepare_successfully(
        package,
        {},
    )

    with flask_app.app_context():
        token = create_package_launch_token(
            execution_data=(
                prepared.execution_data
            ),
            requested_by="alice",
            preview_digest="digest",
        )

        final_character = (
            "A"
            if token[-1] != "A"
            else "B"
        )

        tampered_token = (
            token[:-1]
            + final_character
        )

        with pytest.raises(
            PackageLaunchTokenError
        ):
            read_package_launch_token(
                tampered_token,
                expected_package_id=(
                    package.id
                ),
                expected_username="alice",
            )


def test_package_definition_change_invalidates_preview(
    flask_app,
    credential_key,
):
    package = make_package()

    _, prepared = prepare_successfully(
        package,
        {},
    )

    old_digest = package_definition_digest(
        package
    )

    with flask_app.app_context():
        token = create_package_launch_token(
            execution_data=(
                prepared.execution_data
            ),
            requested_by="alice",
            preview_digest="digest",
        )

        payload = read_package_launch_token(
            token,
            expected_package_id=package.id,
            expected_username="alice",
        )

    assert (
        payload[
            "package_definition_sha256"
        ]
        == old_digest
    )

    package.warning_message = (
        "Definition changed"
    )

    assert (
        package_definition_digest(package)
        != old_digest
    )


def load_runner_module():
    runner_path = (
        Path(__file__)
        .resolve()
        .parents[1]
        / "bin"
        / "journeyman-runner"
    )

    return runpy.run_path(
        str(runner_path)
    )





def test_local_script_execution_honours_shebang(tmp_path):
    runner = load_runner_module()
    script_command = runner["script_command"]

    script = tmp_path / "check.pl"
    script.write_text(
        "#!/usr/bin/perl\nprint \"ok\\n\";\n",
        encoding="utf-8",
    )

    assert script_command(script) == [
        "/usr/bin/perl",
        str(script),
    ]


def test_local_script_execution_falls_back_to_bash_for_sh(tmp_path):
    runner = load_runner_module()
    script_command = runner["script_command"]

    script = tmp_path / "check.sh"
    script.write_text("echo ok\n", encoding="utf-8")

    assert script_command(script) == [
        "/bin/bash",
        str(script),
    ]

def test_runner_environment_variable_credential_is_injected_only_into_child_environment():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    password = "network-password-829174"
    credential_snapshot = SimpleNamespace(
        credential_type="environment_variables",
        credential_name="Cisco networking",
        username="svc-ansiborg",
        get_credential_data=lambda: {
            "password": password,
            "username_environment_variable": "ANSIBLE_NET_USERNAME",
            "secret_environment_variable": "ANSIBLE_NET_PASSWORD",
        },
    )
    step = SimpleNamespace(
        id=77,
        credential_snapshots=[credential_snapshot],
    )
    base_environment = {
        "PATH": "/usr/bin",
        "UNRELATED": "preserved",
    }

    process_environment = build_environment(
        step,
        base_environment=base_environment,
    )

    assert process_environment["ANSIBLE_NET_USERNAME"] == "svc-ansiborg"
    assert process_environment["ANSIBLE_NET_PASSWORD"] == password
    assert process_environment["UNRELATED"] == "preserved"
    assert "ANSIBLE_NET_USERNAME" not in base_environment
    assert "ANSIBLE_NET_PASSWORD" not in base_environment


def test_runner_rejects_multiple_credentials_of_same_type():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    credentials = [
        SimpleNamespace(
            credential_type="environment_variables",
            credential_name="first",
        ),
        SimpleNamespace(
            credential_type="environment_variables",
            credential_name="second",
        ),
    ]

    with pytest.raises(RuntimeError, match="more than one credential"):
        build_environment(
            SimpleNamespace(id=88, credential_snapshots=credentials),
            base_environment={},
        )


def test_runner_machine_credential_injects_become_defaults_into_child_environment():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    credential_snapshot = SimpleNamespace(
        credential_type="machine",
        credential_name="Machine",
        username="ignored",
        get_credential_data=lambda: {
            "become_method": "sudo",
            "become_user": "root",
        },
    )

    base_environment = {"PATH": "/usr/bin"}
    environment = build_environment(
        SimpleNamespace(id=89, credential_snapshots=[credential_snapshot]),
        base_environment=base_environment,
    )

    assert environment["PATH"] == "/usr/bin"
    assert environment["JOURNEYMAN_STEP_ID"] == "89"
    assert environment["ANSIBLE_BECOME_METHOD"] == "sudo"
    assert environment["ANSIBLE_BECOME_USER"] == "root"
    assert "ANSIBLE_BECOME_METHOD" not in base_environment
    assert "ANSIBLE_BECOME_USER" not in base_environment


def test_runner_extra_vars_file_is_protected_and_removed(
    tmp_path,
    credential_key,
):
    runner = load_runner_module()

    materialize = runner[
        "materialize_job_package_extra_vars"
    ]

    remove_file = runner[
        "remove_job_extra_vars_file"
    ]

    build_command = runner[
        "build_command"
    ]

    secret_value = (
        "runner-secret-483901"
    )

    job_directory = (
        tmp_path / "job"
    )

    job_directory.mkdir(
        mode=0o700
    )

    job = SimpleNamespace(
        id=500,
        package_snapshot=SimpleNamespace(
            get_execution_vars=lambda: {
                "device": "switch01",
                "password": secret_value,
            }
        ),
    )

    extra_vars_path = materialize(
        job,
        job_directory,
    )

    assert (
        os.stat(extra_vars_path).st_mode
        & 0o777
    ) == 0o600

    assert (
        os.stat(extra_vars_path.parent).st_mode
        & 0o777
    ) == 0o700

    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "playbook.yml").write_text(
        "---\n- hosts: all\n  tasks: []\n"
    )

    step = SimpleNamespace(
        playbook="playbook.yml",
        verbosity=0,
        limit="",
        tags="",
        skip_tags="",
    )

    command = build_command(
        step,
        repository_path,
        tmp_path / "inventory.py",
        extra_vars_path,
    )

    command_text = " ".join(
        str(item)
        for item in command
    )

    assert "--extra-vars" in command
    assert (
        "@{}".format(extra_vars_path)
        in command
    )

    assert secret_value not in command_text
    assert "switch01" not in command_text

    remove_file(
        extra_vars_path
    )

    assert not extra_vars_path.exists()
    assert not extra_vars_path.parent.exists()


def test_runner_manifest_does_not_contain_secret(
    tmp_path,
    credential_key,
):
    runner = load_runner_module()

    materialize = runner[
        "materialize_job_package_extra_vars"
    ]

    remove_file = runner[
        "remove_job_extra_vars_file"
    ]

    write_manifest = runner[
        "write_manifest"
    ]

    secret_value = (
        "manifest-secret-963852"
    )

    job_directory = (
        tmp_path / "job"
    )

    repository_path = (
        job_directory
        / "repositories"
        / "11"
    )

    inventory_path = (
        job_directory
        / "inventories"
        / "22.py"
    )

    repository_path.mkdir(
        parents=True,
        mode=0o700,
    )

    inventory_path.parent.mkdir(
        parents=True,
        mode=0o700,
    )

    playbook_path = (
        repository_path
        / "playbook.yml"
    )

    playbook_path.write_text(
        "---\n- hosts: all\n  tasks: []\n",
        encoding="utf-8",
    )

    inventory_path.write_text(
        "#!/usr/bin/env python3\n",
        encoding="utf-8",
    )

    repository_snapshot = (
        SimpleNamespace(
            id=11,
            repository_id=1,
            repository_name="Test repository",
            repository_url="git@example/test.git",
            repository_commit="abc123",
        )
    )

    inventory_snapshot = (
        SimpleNamespace(
            id=22,
            inventory_id=2,
            inventory_name="Test inventory",
            inventory_type="static",
            version=1,
            host_count=1,
            content_sha256="0" * 64,
        )
    )

    step = SimpleNamespace(
        id=33,
        position=1,
        name="Test step",
        job_repository_snapshot_id=11,
        job_inventory_snapshot_id=22,
        playbook="playbook.yml",
        limit="",
        tags="",
        skip_tags="",
        verbosity=0,
        continue_on_failure=False,
        get_dependency_positions=lambda: [],
    )

    package_snapshot = (
        SimpleNamespace(
            id=44,
            package_id=100,
            package_name="Test Package",
            package_owner="owner",
            package_definition_sha256=(
                "1" * 64
            ),
            step_limit="",
            get_execution_vars=lambda: {
                "password": secret_value,
            },
            get_display_values=lambda: [],
            get_operational_targets=lambda: [],
        )
    )

    job = SimpleNamespace(
        id=55,
        project_id=200,
        project_name="Test Project",
        requested_by="alice",
        queued_at=datetime.now(
            timezone.utc
        ),
        repository_snapshots=[
            repository_snapshot,
        ],
        inventory_snapshots=[
            inventory_snapshot,
        ],
        steps=[
            step,
        ],
        package_snapshot=(
            package_snapshot
        ),
    )

    extra_vars_path = materialize(
        job,
        job_directory,
    )

    manifest_path = write_manifest(
        job,
        job_directory,
        {
            11: repository_path,
        },
        {
            22: inventory_path,
        },
        extra_vars_path,
    )

    manifest_text = (
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    manifest = json.loads(
        manifest_text
    )

    assert secret_value not in manifest_text

    assert (
        manifest["package"][
            "uses_encrypted_extra_vars"
        ]
        is True
    )

    assert (
        manifest["package"][
            "display_values"
        ]
        == []
    )

    command_text = json.dumps(
        manifest["steps"][0]["command"]
    )

    assert secret_value not in command_text

    assert (
        "@{}".format(extra_vars_path)
        in manifest["steps"][0]["command"]
    )

    remove_file(
        extra_vars_path
    )

    assert not extra_vars_path.exists()


def test_package_launch_permission_supports_ad_object_guids(flask_app):
    with flask_app.app_context():
        package = make_package(
            package_id=999,
        )

        user_guid = (
            "11111111-1111-1111-1111-111111111111"
        )
        team_guid = (
            "22222222-2222-2222-2222-222222222222"
        )

        user_permission = add_permission(
            package,
            PACKAGE_PRINCIPAL_USER,
            "renamed.user",
        )
        user_permission.principal_object_guid = user_guid

        team_permission = add_permission(
            package,
            PACKAGE_PRINCIPAL_GROUP,
            "Renamed Team",
        )
        team_permission.principal_object_guid = team_guid

        assert can_launch_package(
            package,
            username="old.username",
            group_names=(),
            user_object_guid=user_guid,
            group_object_guids=(),
            is_admin=False,
        )

        assert can_launch_package(
            package,
            username="someone.else",
            group_names=(),
            user_object_guid=None,
            group_object_guids={team_guid},
            is_admin=False,
        )

def test_guid_permission_does_not_fall_back_to_reused_name():
    package = make_package(
        package_id=1000,
    )

    permission = add_permission(
        package,
        PACKAGE_PRINCIPAL_USER,
        "alice",
    )
    permission.principal_object_guid = (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    assert not can_launch_package(
        package,
        username="alice",
        group_names=(),
        user_object_guid=(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        ),
        group_object_guids=(),
        is_admin=False,
    )


def test_email_addresses_are_normalised_and_bound(flask_app):
    with flask_app.app_context():
        package = make_package()
        email_input = add_input(
            package,
            input_id=901,
            position=1,
            variable_name="recipients",
            label="Recipients",
            input_type=PACKAGE_INPUT_EMAIL_ADDRESSES,
            required=True,
        )

        fields, prepared = prepare_successfully(
            package,
            {
                form_field(email_input): (
                    "alice@example.com; Bob@example.com\n"
                    "alice@example.com,carol@example.net"
                ),
            },
        )

        assert prepared.execution_data.execution_vars[
            "recipients"
        ] == [
            "alice@example.com",
            "Bob@example.com",
            "carol@example.net",
        ]
        assert fields[0]["value"] == (
            "alice@example.com\n"
            "Bob@example.com\n"
            "carol@example.net"
        )


def test_email_addresses_reject_invalid_item(flask_app):
    with flask_app.app_context():
        package = make_package()
        email_input = add_input(
            package,
            input_id=902,
            position=1,
            variable_name="recipients",
            label="Recipients",
            input_type=PACKAGE_INPUT_EMAIL_ADDRESSES,
            required=True,
        )

        errors, _fields, prepared = prepare_package_launch(
            package=package,
            form=MultiDict(
                {
                    form_field(email_input): (
                        "alice@example.com, bob@example"
                    ),
                }
            ),
        )

        assert prepared is None
        assert errors == [
            "Recipients contains an invalid email address: "
            "bob@example."
        ]


def test_runner_maps_shell_package_inputs_to_environment():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    package_snapshot = SimpleNamespace(
        get_package_definition=lambda: {
            "inputs": [
                {
                    "variable_name": "device",
                    "binding_type": "extra_var",
                },
                {
                    "variable_name": "write_memory",
                    "binding_type": "extra_var",
                },
                {
                    "variable_name": "password",
                    "binding_type": "extra_var",
                    "is_secret": True,
                },
                {
                    "variable_name": "host_limit",
                    "binding_type": "step_limit",
                },
            ],
        },
        get_execution_vars=lambda: {
            "device": "switch01",
            "write_memory": True,
            "password": "secret-value",
            "fixed_setting": "not-exported",
        },
    )

    job = SimpleNamespace(
        execution_type="shell",
        package_snapshot=package_snapshot,
    )

    step = SimpleNamespace(
        id=90,
        job_id=50,
        name="Run script",
        job=job,
        credential_snapshots=[],
    )

    environment = build_environment(
        step,
        base_environment={"PATH": "/usr/bin"},
    )

    assert environment == {
        "PATH": "/usr/bin",
        "JOURNEYMAN_JOB_ID": "50",
        "JOURNEYMAN_STEP_ID": "90",
        "JOURNEYMAN_STEP_NAME": "Run script",
        "JOURNEYMAN_INPUT_DEVICE": "switch01",
        "JOURNEYMAN_INPUT_WRITE_MEMORY": "true",
        "JOURNEYMAN_INPUT_PASSWORD": "secret-value",
    }


def test_runner_does_not_map_package_inputs_for_ansible_job():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    package_snapshot = SimpleNamespace(
        get_package_definition=lambda: {
            "inputs": [
                {
                    "variable_name": "device",
                    "binding_type": "extra_var",
                },
            ],
        },
        get_execution_vars=lambda: {
            "device": "switch01",
        },
    )

    step = SimpleNamespace(
        id=91,
        job=SimpleNamespace(
            execution_type="ansible",
            package_snapshot=package_snapshot,
        ),
        credential_snapshots=[],
    )

    environment = build_environment(step, base_environment={})

    assert environment == {
        "JOURNEYMAN_STEP_ID": "91",
        "ANSIBLE_CONFIG": "/etc/ansible/ansible.cfg",
    }


def test_runner_rejects_case_colliding_shell_input_names():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    package_snapshot = SimpleNamespace(
        get_package_definition=lambda: {
            "inputs": [
                {
                    "variable_name": "device",
                    "binding_type": "extra_var",
                },
                {
                    "variable_name": "DEVICE",
                    "binding_type": "extra_var",
                },
            ],
        },
        get_execution_vars=lambda: {
            "device": "one",
            "DEVICE": "two",
        },
    )

    step = SimpleNamespace(
        id=92,
        job=SimpleNamespace(
            execution_type="shell",
            package_snapshot=package_snapshot,
        ),
        credential_snapshots=[],
    )

    with pytest.raises(RuntimeError, match="collide"):
        build_environment(step, base_environment={})


def test_runner_serializes_structured_shell_input_values():
    runner = load_runner_module()
    stringify = runner["_shell_input_environment_value"]

    assert stringify(False) == "false"
    assert stringify(12) == "12"
    assert stringify(["a@example.com", "b@example.com"]) == (
        '["a@example.com","b@example.com"]'
    )


def test_runner_sets_snapshotted_ansible_config_for_ansible_job():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]
    step = SimpleNamespace(
        id=150,
        job_id=33,
        name="Configured playbook",
        job=SimpleNamespace(execution_type="ansible", package_snapshot=None),
        ansible_config_path="/etc/ansible/network.cfg",
        credential_snapshots=[],
    )
    environment = build_environment(step, base_environment={"PATH": "/usr/bin"})
    assert environment["ANSIBLE_CONFIG"] == "/etc/ansible/network.cfg"


def test_runner_does_not_set_ansible_config_for_shell_job():
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]
    step = SimpleNamespace(
        id=151,
        job_id=34,
        name="Shell script",
        job=SimpleNamespace(execution_type="shell", package_snapshot=None),
        ansible_config_path="/etc/ansible/network.cfg",
        credential_snapshots=[],
    )
    environment = build_environment(step, base_environment={"PATH": "/usr/bin"})
    assert "ANSIBLE_CONFIG" not in environment


def test_remote_shell_generated_playbook_supports_become_and_serial(tmp_path):
    runner = load_runner_module()
    build_playbook = runner["materialize_remote_shell_playbook"]

    repository = tmp_path / "repositories" / "1"
    repository.mkdir(parents=True)
    script = repository / "scripts" / "check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")

    step = SimpleNamespace(
        id=77,
        playbook="scripts/check.sh",
        remote_shell_become=True,
        remote_shell_serial=5,
        job=SimpleNamespace(
            execution_type="remote_shell",
            package_snapshot=None,
        ),
    )

    playbook_path = build_playbook(step, repository)
    playbook = json.loads(playbook_path.read_text(encoding="utf-8"))

    assert playbook[0]["become"] is True
    assert playbook[0]["serial"] == 5
    assert playbook[0]["tasks"][0]["ansible.builtin.script"]["cmd"].endswith(
        "/scripts/check.sh"
    )


def test_runner_materializes_machine_credential_without_command_line_secrets(
    tmp_path,
):
    runner = load_runner_module()
    materialize = runner["materialize_machine_credential_extra_vars"]
    build_command = runner["build_command"]

    password = "ssh-password-937441"
    become_password = "become-password-284116"
    private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "test-private-key-material\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )

    credential_snapshot = SimpleNamespace(
        credential_type="machine",
        credential_name="Linux machine",
        username="svc-journeyman",
        get_credential_data=lambda: {
            "password": password,
            "ssh_private_key": private_key,
            "ssh_key_passphrase": "key-passphrase-5129",
            "become_password": become_password,
            "become_method": "sudo",
            "become_user": "root",
        },
    )

    step = SimpleNamespace(
        id=88,
        job=SimpleNamespace(execution_type="remote_shell"),
        credential_snapshots=[credential_snapshot],
        playbook="scripts/check.sh",
        verbosity=0,
        limit="",
        tags="",
        skip_tags="",
        environment_path="__SYSTEM_ANSIBLE__",
    )

    job_directory = tmp_path / "job"
    repository = job_directory / "repositories" / "1"
    repository.mkdir(parents=True)
    script = repository / "scripts" / "check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")

    inventory = job_directory / "inventory.json"
    inventory.write_text(
        '{"all":{"hosts":["host01"]},"_meta":{"hostvars":{"host01":{}}}}\n',
        encoding="utf-8",
    )

    variables_path = materialize(step, job_directory)
    assert variables_path is not None
    assert (variables_path.stat().st_mode & 0o777) == 0o600

    values = json.loads(variables_path.read_text(encoding="utf-8"))
    assert values["ansible_user"] == "svc-journeyman"
    assert values["ansible_password"] == password
    assert values["ansible_become_password"] == become_password
    assert "ansible_become_method" not in values
    assert "ansible_become_user" not in values
    assert values["ansible_private_key_passphrase"] == "key-passphrase-5129"

    key_path = Path(values["ansible_private_key_file"])
    assert key_path.read_text(encoding="utf-8") == private_key
    assert (key_path.stat().st_mode & 0o777) == 0o600

    command = build_command(
        step,
        repository,
        inventory,
        machine_extra_vars_path=variables_path,
    )

    command_text = " ".join(command)
    assert password not in command_text
    assert become_password not in command_text
    assert private_key not in command_text
    assert "@{}".format(variables_path) in command


def test_runner_namespaces_linux_machine_credential_when_environment_credential_is_also_selected(
    tmp_path,
):
    runner = load_runner_module()
    materialize = runner["materialize_machine_credential_extra_vars"]

    machine_snapshot = SimpleNamespace(
        credential_type="machine",
        credential_name="Linux machine",
        username="svc-linux",
        get_credential_data=lambda: {
            "password": "linux-password",
            "ssh_private_key": "PRIVATE KEY DATA\n",
            "become_method": "sudo",
            "become_user": "root",
        },
    )
    network_snapshot = SimpleNamespace(
        credential_type="environment_variables",
        credential_name="Cisco network",
        username="svc-ansiborg",
        get_credential_data=lambda: {
            "password": "network-password",
            "username_environment_variable": "ANSIBLE_NET_USERNAME",
            "secret_environment_variable": "ANSIBLE_NET_PASSWORD",
        },
    )
    step = SimpleNamespace(
        id=188,
        job=SimpleNamespace(execution_type="ansible"),
        credential_snapshots=[machine_snapshot, network_snapshot],
    )

    variables_path = materialize(step, tmp_path / "job")
    values = json.loads(variables_path.read_text(encoding="utf-8"))

    assert values["linux_ansible_user"] == "svc-linux"
    assert values["linux_ansible_password"] == "linux-password"
    assert "linux_ansible_become_method" not in values
    assert "linux_ansible_become_user" not in values
    assert "linux_ansible_private_key_file" in values
    assert "ansible_user" not in values
    assert "ansible_password" not in values
    assert "ansible_private_key_file" not in values


def test_runner_does_not_materialize_machine_credential_for_local_shell(
    tmp_path,
):
    runner = load_runner_module()
    materialize = runner["materialize_machine_credential_extra_vars"]

    credential_snapshot = SimpleNamespace(
        credential_type="machine",
        credential_name="Unused machine",
        username="svc-journeyman",
        get_credential_data=lambda: {"password": "must-not-be-written"},
    )

    step = SimpleNamespace(
        id=89,
        job=SimpleNamespace(execution_type="shell"),
        credential_snapshots=[credential_snapshot],
    )

    assert materialize(step, tmp_path) is None
    assert not (tmp_path / "private").exists()


def test_package_choice_rejects_value_not_offered_by_server(flask_app):
    package = make_package()
    cluster = add_input(
        package,
        input_id=9001,
        position=1,
        variable_name="cluster",
        label="Cluster",
        input_type=PACKAGE_INPUT_CHOICE,
        required=True,
        choices=[
            {"value": "lab01", "label": "LAB01"},
            {"value": "lab02", "label": "LAB02"},
        ],
    )

    with flask_app.app_context():
        errors, _fields, prepared = prepare_package_launch(
            package=package,
            form=MultiDict(
                {
                    form_field(cluster): json.dumps("prd99"),
                }
            ),
        )

    assert prepared is None
    assert any("Cluster contains an invalid choice." in error for error in errors)


def test_runner_materializes_windows_credential_as_namespaced_extra_vars(tmp_path):
    runner = load_runner_module()
    materialize = runner["materialize_windows_credential_extra_vars"]
    build_command = runner["build_command"]

    password = "windows-password-72114"
    snapshot = SimpleNamespace(
        credential_type="windows",
        credential_name="Windows domain",
        username="DOMAIN\\svc-journeyman",
        get_credential_data=lambda: {
            "password": password,
            "extra_vars": {
                "win_ansible_user": "{{ user }}",
                "win_ansible_password": "{{ passwd }}",
                "win_ansible_connection": "winrm",
                "win_ansible_ssh_port": "5985",
            },
        },
    )
    step = SimpleNamespace(
        id=90,
        job=SimpleNamespace(execution_type="ansible"),
        credential_snapshots=[snapshot],
        playbook="playbook.yml",
        verbosity=0,
        limit="",
        tags="",
        skip_tags="",
        environment_path="__SYSTEM_ANSIBLE__",
    )

    job_directory = tmp_path / "job"
    repository = job_directory / "repositories" / "1"
    repository.mkdir(parents=True)
    (repository / "playbook.yml").write_text(
        "---\n- hosts: all\n  tasks: []\n",
        encoding="utf-8",
    )
    inventory = job_directory / "inventory.json"
    inventory.write_text(
        '{"all":{"hosts":["host01"]},"_meta":{"hostvars":{"host01":{}}}}\n',
        encoding="utf-8",
    )

    variables_path = materialize(step, job_directory)
    values = json.loads(variables_path.read_text(encoding="utf-8"))
    assert values["win_ansible_user"] == "DOMAIN\\svc-journeyman"
    assert values["win_ansible_password"] == password
    assert values["win_ansible_connection"] == "winrm"
    assert values["win_ansible_ssh_port"] == "5985"
    assert "ansible_user" not in values
    assert "ansible_password" not in values
    assert (variables_path.stat().st_mode & 0o777) == 0o600

    command = build_command(
        step,
        repository,
        inventory,
        windows_extra_vars_path=variables_path,
    )
    command_text = " ".join(command)
    assert password not in command_text
    assert "@{}".format(variables_path) in command


def test_local_runner_uses_per_job_writable_ansible_runtime(tmp_path):
    runner = load_runner_module()
    build_environment = runner["build_step_process_environment"]

    job = SimpleNamespace(execution_type="ansible")
    step = SimpleNamespace(
        id=901,
        job_id=77,
        name="Manage remote runner",
        job=job,
        ansible_config_path="/etc/ansible/ansible.cfg",
        credential_snapshots=[],
    )

    process_environment = build_environment(
        step,
        base_environment={"PATH": "/usr/bin"},
        workspace=tmp_path,
    )

    ansible_home = tmp_path / "private" / "ansible"
    assert process_environment["ANSIBLE_HOME"] == str(ansible_home)
    assert process_environment["ANSIBLE_LOCAL_TEMP"] == str(ansible_home / "tmp")
    assert process_environment["ANSIBLE_SSH_CONTROL_PATH_DIR"] == str(ansible_home / "cp")
    assert (ansible_home / "tmp").is_dir()
    assert (ansible_home / "cp").is_dir()
