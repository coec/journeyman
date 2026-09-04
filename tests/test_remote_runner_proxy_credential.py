from app import db
from app.models import Credential
from app.services.builtin_automation import ensure_builtin_admin_automation
from app.services.project_package_launch import (
    create_package_launch_token,
    package_execution_from_token,
    prepare_package_launch,
    read_package_launch_token,
)
from app.views.packages import (
    _managed_runner_context,
    _resolve_managed_runner_bootstrap_credential,
    _resolve_managed_runner_proxy_credential,
)


def test_remote_runner_proxy_credential_is_resolved_only_for_execution(app):
    with app.app_context():
        bootstrap = Credential(
            name="svc_journeyman",
            owner="admin",
            credential_type="machine",
            username="svc_journeyman",
        )
        bootstrap.set_credential_data({
            "password": "ssh-secret",
            "become_password": "sudo-secret",
        })
        credential = Credential(
            name="Authenticated pip proxy",
            owner="admin",
            credential_type="url",
            username="proxy user",
        )
        credential.set_credential_data({
            "url": "http://proxy.example.com:8080",
            "auth_mode": "basic",
            "password": "p@ss:word",
        })
        db.session.add_all([bootstrap, credential])
        db.session.commit()

        package = ensure_builtin_admin_automation()["package"]
        inputs = {item.variable_name: item for item in package.inputs}
        form = {
            "package_value_{}".format(inputs["journeyman_manage_action"].id): '"install"',
            "package_value_{}".format(inputs["journeyman_runner_host"].id): "runner01.example.com",
            "package_value_{}".format(inputs["journeyman_runner_name"].id): "runner01.example.com",
            "package_value_{}".format(inputs["journeyman_bootstrap_credential_id"].id): str(bootstrap.id),
            "package_value_{}".format(inputs["journeyman_runner_max_concurrent_steps"].id): "1",
            "package_value_{}".format(inputs["journeyman_pip_proxy_required"].id): "true",
            "package_value_{}".format(inputs["journeyman_pip_proxy_credential_id"].id): str(credential.id),
        }

        errors, _fields, prepared = prepare_package_launch(
            package=package,
            form=form,
        )
        assert errors == []

        resolved = _resolve_managed_runner_bootstrap_credential(package, prepared)
        assert resolved.execution_data.machine_credential_override_id == bootstrap.id
        assert resolved.execution_data.execution_vars["journeyman_bootstrap_credential_id"] == bootstrap.id

        resolved = _resolve_managed_runner_proxy_credential(package, resolved)
        execution_vars = resolved.execution_data.execution_vars
        assert execution_vars["journeyman_pip_proxy_credential_id"] == credential.id
        assert execution_vars["journeyman_pip_proxy_url"] == (
            "http://proxy%20user:p%40ss%3Aword@proxy.example.com:8080"
        )
        assert all(
            item["variable_name"] != "journeyman_pip_proxy_url"
            for item in resolved.execution_data.display_values
        )

        token = create_package_launch_token(
            execution_data=resolved.execution_data,
            requested_by="admin",
            preview_digest="digest",
        )
        payload = read_package_launch_token(
            token,
            expected_package_id=package.id,
            expected_username="admin",
        )
        restored = package_execution_from_token(package=package, payload=payload)
        assert restored.machine_credential_override_id == bootstrap.id


def test_existing_runner_prefills_saved_management_credentials(app):
    from app.models import Runner
    from app.services.project_package_launch import package_launch_fields

    with app.app_context():
        bootstrap = Credential(
            name="svc_journeyman",
            owner="admin",
            credential_type="machine",
            username="svc_journeyman",
        )
        bootstrap.set_credential_data({"password": "secret", "become_password": "sudo"})
        proxy = Credential(
            name="Runner pip proxy",
            owner="admin",
            credential_type="url",
        )
        proxy.set_credential_data({
            "url": "http://proxy.example.com:8080",
            "auth_mode": "none",
        })
        db.session.add_all([bootstrap, proxy])
        db.session.flush()
        runner = Runner(
            name="dev-runner-1",
            hostname="runner01.example.com",
            management_bootstrap_credential_id=bootstrap.id,
            management_pip_proxy_required=True,
            management_pip_proxy_credential_id=proxy.id,
        )
        db.session.add(runner)
        package = ensure_builtin_admin_automation()["package"]
        db.session.commit()

        with app.test_request_context(
            f"/packages/{package.id}/launch?runner_id={runner.id}&action=update"
        ):
            fields = _managed_runner_context(package, package_launch_fields(package))

        by_name = {field["variable_name"]: field for field in fields}
        bootstrap_field = by_name["journeyman_bootstrap_credential_id"]
        proxy_required_field = by_name["journeyman_pip_proxy_required"]
        proxy_field = by_name["journeyman_pip_proxy_credential_id"]

        assert next(
            item for item in bootstrap_field["choices"]
            if item["key"] == bootstrap_field["selected_choice_key"]
        )["value"] == bootstrap.id
        assert proxy_required_field["checked"] is True
        assert next(
            item for item in proxy_field["choices"]
            if item["key"] == proxy_field["selected_choice_key"]
        )["value"] == proxy.id
