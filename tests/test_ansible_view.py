from types import SimpleNamespace

import yaml

from app.services.ansible_view import (
    _dump_task,
    dispatch_yaml,
    project_configuration_params,
)


def _named(name):
    return SimpleNamespace(name=name)


def test_project_configuration_params_preserve_credential_inheritance():
    inherited = SimpleNamespace(
        name="Inherited",
        repository=None,
        inventory=None,
        environment=None,
        credentials_override=False,
        credentials=[],
        playbook="inherited.yml",
        limit="",
        tags="",
        skip_tags="",
        verbosity=0,
        check_mode=False,
        continue_on_failure=False,
        failure_only=False,
        refresh_repository=False,
        refresh_inventory_after=False,
        oversight_after=False,
        enabled=True,
        get_extra_vars=lambda: {},
        get_dependency_positions=lambda: [],
    )
    overridden = SimpleNamespace(
        name="Override",
        repository=None,
        inventory=None,
        environment=None,
        credentials_override=True,
        credentials=[_named("Step Credential")],
        playbook="override.yml",
        limit="",
        tags="",
        skip_tags="",
        verbosity=1,
        check_mode=False,
        continue_on_failure=False,
        failure_only=False,
        refresh_repository=False,
        refresh_inventory_after=False,
        oversight_after=False,
        enabled=True,
        get_extra_vars=lambda: {"mode": "test"},
        get_dependency_positions=lambda: [1],
    )
    project = SimpleNamespace(
        name="SMIT Project",
        description="Rendered as Ansible",
        execution_type="ansible",
        inventory=_named("Linux"),
        repository=_named("Automation"),
        environment=_named("Modern Ansible"),
        credentials=[_named("Machine")],
        max_parallel_steps=4,
        concurrency_policy="exclusive",
        oversight_required_between_all_steps=False,
        enabled=True,
        steps=[inherited, overridden],
    )

    params = project_configuration_params(project)

    assert "credentials" not in params["steps"][0]
    assert params["steps"][1]["credentials"] == ["Step Credential"]
    assert params["steps"][1]["depends_on"] == ["Inherited"]
    assert params["state"] == "present"



def test_show_ansible_uses_literal_blocks_for_multiline_strings():
    rendered = _dump_task(
        "Configure Journeyman Inventory: client.local",
        "journeyman.configuration.inventory",
        {
            "name": "client.local",
            "inventory_type": "static",
            "content": "all:\r\n  hosts:\r\n    client.local:",
            "state": "present",
        },
    )

    assert "content: |\n" in rendered
    assert "all:\n        hosts:\n          client.local:" in rendered
    assert "\\n" not in rendered

    parsed = yaml.safe_load(rendered)
    assert parsed[0]["journeyman.configuration.inventory"]["content"] == (
        "all:\n  hosts:\n    client.local:\n"
    )

def test_dispatch_yaml_uses_operation_collection():
    rendered = dispatch_yaml("project", "Patch Servers")
    parsed = yaml.safe_load(rendered)

    assert parsed == [
        {
            "name": "Dispatch Journeyman Project: Patch Servers",
            "journeyman.operation.dispatch": {
                "type": "project",
                "name": "Patch Servers",
            },
        }
    ]


def test_show_ansible_template_renders_one_selected_variant():
    template = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app" / "templates" / "show_ansible.html"
    ).read_text(encoding="utf-8")

    assert "{{ ansible_kind }}" in template
    assert "{{ ansible_yaml }}" in template
    assert "configuration_yaml" not in template
    assert "dispatch_yaml" not in template
