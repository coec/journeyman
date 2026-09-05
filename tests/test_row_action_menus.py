from pathlib import Path

from tests.checks import assert_output_contains, assert_output_excludes


ROOT = Path(__file__).resolve().parents[1]


def _template(name):
    return (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")


def test_multi_action_table_rows_use_common_action_menu():
    templates = {
        "projects.html": [
            "Dispatch", "Schedule", "Clone", "Show Ansible",
            "Configuration", "Operation", "Edit", "Delete",
        ],
        "project_packages.html": [
            "Dispatch", "Show Ansible", "Configuration", "Operation",
            "Edit", "Delete",
        ],
        "schedules.html": ["Dispatch now", "Edit", "Disable", "Delete"],
        "environments.html": ["Validate", "Make default", "Delete"],
        "teams.html": ["Members", "Remove"],
        "runners.html": ["Manage", "Disable"],
        "inventories.html": ["Refresh", "Inspect", "Edit", "Clone", "Delete"],
    }

    for name, expected_actions in templates.items():
        content = _template(name)
        assert_output_contains(
            content,
            'class="action-menu"',
            purpose="{} groups multiple row operations into the shared dropdown".format(name),
        )
        assert_output_contains(
            content,
            "Actions <span",
            purpose="{} labels the common per-row control as Actions".format(name),
        )
        for action in expected_actions:
            assert_output_contains(
                content,
                action,
                purpose="{} retains the {} row operation inside the dropdown".format(name, action),
            )


def test_show_ansible_uses_configuration_and_operation_submenu():
    for name in ("projects.html", "project_packages.html"):
        content = _template(name)
        assert_output_contains(
            content,
            'class="action-submenu"',
            purpose="{} groups Show Ansible variants into a nested submenu".format(name),
        )
        assert_output_contains(
            content,
            ">Configuration</a>",
            purpose="{} exposes the declarative collection invocation".format(name),
        )
        assert_output_contains(
            content,
            ">Operation</a>",
            purpose="{} exposes the runtime dispatch invocation".format(name),
        )


def test_runner_management_playbook_updates_signal_capable_runner_components():
    playbook = (ROOT / "deploy" / "ansible" / "manage-remote-runner.yml").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "bin" / "journeyman-remote-runner").read_text(encoding="utf-8")

    assert_output_contains(
        playbook,
        "journeyman_manage_action in ['install', 'update']",
        purpose="The built-in runner management workflow has an explicit software update path",
    )
    assert_output_contains(
        playbook,
        "journeyman-signal-spool",
        purpose="Install/update deploys the Signal spool helper required by Reaction Sources",
    )
    assert_output_contains(
        playbook,
        "ProtectSystem=full",
        purpose=(
            "Updated runner service protects system paths while permitting "
            "general-purpose automation writes"
        ),
    )
    assert_output_contains(
        playbook,
        "--expected-version",
        purpose="The update workflow waits for a heartbeat from the expected runner version",
    )
    assert_output_contains(
        runner,
        'VERSION = "0.16"',
        purpose="The SNMP-capable runner release is versioned distinctly from earlier runner releases",
    )
