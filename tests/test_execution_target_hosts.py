import json
from types import SimpleNamespace

from app.services.execution_target_hosts import target_hosts_for_inventory


def _inventory():
    return {
        "all": {"hosts": ["host01", "host02"], "children": []},
        "_meta": {"hostvars": {"host01": {}, "host02": {}}},
    }


def test_target_hosts_uses_ansible_playbook_limit_and_returns_only_selected_hosts(
    app,
    monkeypatch,
):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "\nplaybook: /tmp/list-hosts.yml\n\n"
                "  play #1 (all): Journeyman target resolution\tTAGS: []\n"
                "    pattern: ['all']\n"
                "    hosts (1):\n"
                "      host02\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "app.services.execution_target_hosts.subprocess.run",
        fake_run,
    )

    with app.app_context():
        hosts = target_hosts_for_inventory(_inventory(), "host02")

    assert hosts == ("host02",)
    assert captured["command"][0].endswith("/ansible-playbook")
    assert "--list-hosts" in captured["command"]
    assert captured["command"][-3:-1] == ["--limit", "host02"]


def test_target_hosts_without_limit_still_uses_ansible_inventory_list(
    app,
    monkeypatch,
):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_inventory()),
            stderr="",
        )

    monkeypatch.setattr(
        "app.services.execution_target_hosts.subprocess.run",
        fake_run,
    )

    with app.app_context():
        hosts = target_hosts_for_inventory(_inventory())

    assert hosts == ("host01", "host02")
    assert captured["command"][-1] == "--list"
    assert "--limit" not in captured["command"]
