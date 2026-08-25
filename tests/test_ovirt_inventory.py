import json
from types import SimpleNamespace

import pytest

from app.services import ovirt_inventory


class _Credential:
    credential_type = "url"
    username = "admin@internal"

    def get_credential_data(self):
        return {
            "url": "https://engine.example.org/ovirt-engine/api",
            "auth_mode": "basic",
            "password": "super-secret",
            "token_prefix": "",
        }


def test_ovirt_inventory_uses_upstream_plugin_and_environment_credentials(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        with open(argv[2], encoding="utf-8") as handle:
            captured["source"] = handle.read()
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "_meta": {"hostvars": {"vm01.example.org": {"cluster": "PROD"}}},
                "all": {"children": ["ungrouped"]},
                "ungrouped": {"hosts": ["vm01.example.org"]},
            }),
            stderr="",
        )

    monkeypatch.setattr(ovirt_inventory.subprocess, "run", fake_run)
    result = ovirt_inventory.resolve_ovirt_inventory(
        credential=_Credential(),
        verify_tls=True,
        query_filter={"search": "cluster=PROD"},
    )

    assert result["_meta"]["hostvars"]["vm01.example.org"]["cluster"] == "PROD"
    assert "plugin: ovirt.ovirt.ovirt" in captured["source"]
    assert "ovirt_insecure: false" in captured["source"]
    assert "cluster=PROD" in captured["source"]
    assert "super-secret" not in captured["source"]
    assert captured["env"]["OVIRT_URL"] == "https://engine.example.org/ovirt-engine/api"
    assert captured["env"]["OVIRT_USERNAME"] == "admin@internal"
    assert captured["env"]["OVIRT_PASSWORD"] == "super-secret"


def test_ovirt_inventory_requires_basic_url_credential(monkeypatch):
    monkeypatch.setattr(
        ovirt_inventory,
        "url_credential_details",
        lambda credential: ("user", {"url": "https://engine/api", "auth_mode": "bearer", "token": "x"}),
    )
    with pytest.raises(ovirt_inventory.OvirtInventoryError, match="Basic authentication"):
        ovirt_inventory.resolve_ovirt_inventory(credential=object())


def test_ovirt_inventory_redacts_credentials_from_ansible_error(monkeypatch):
    monkeypatch.setattr(
        ovirt_inventory.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="failed https://engine.example.org/ovirt-engine/api admin@internal super-secret",
        ),
    )
    with pytest.raises(ovirt_inventory.OvirtInventoryError) as excinfo:
        ovirt_inventory.resolve_ovirt_inventory(credential=_Credential())
    text = str(excinfo.value)
    assert "super-secret" not in text
    assert "admin@internal" not in text
    assert "engine.example.org" not in text
