import json
from types import SimpleNamespace
import pytest
from app.services import netbox_inventory


class _Credential:
    credential_type = "url"
    username = ""
    def get_credential_data(self):
        return {
            "url": "https://netbox.example.org",
            "auth_mode": "token",
            "token": "super-secret-token",
            "token_prefix": "Token",
        }


def test_netbox_inventory_uses_upstream_plugin_and_environment_token(monkeypatch):
    captured = {}
    def fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        with open(argv[2], encoding="utf-8") as handle:
            captured["source"] = handle.read()
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "_meta": {"hostvars": {"sw01": {"interfaces": [{"name": "Gi1/0/1"}]}}},
            "all": {"children": ["ungrouped"]}, "ungrouped": {"hosts": ["sw01"]},
        }), stderr="")
    monkeypatch.setattr(netbox_inventory.subprocess, "run", fake_run)
    result = netbox_inventory.resolve_netbox_inventory(credential=_Credential())
    assert result["_meta"]["hostvars"]["sw01"]["interfaces"][0]["name"] == "Gi1/0/1"
    assert "plugin: netbox.netbox.nb_inventory" in captured["source"]
    assert "interfaces: true" in captured["source"]
    assert "services: true" in captured["source"]
    assert "super-secret-token" not in captured["source"]
    assert captured["env"]["NETBOX_API"] == "https://netbox.example.org"
    assert captured["env"]["NETBOX_TOKEN"] == "super-secret-token"


def test_netbox_inventory_requires_token_credential(monkeypatch):
    monkeypatch.setattr(netbox_inventory, "url_credential_details", lambda credential: ("", {
        "url": "https://netbox.example.org", "auth_mode": "bearer", "token": "x"
    }))
    with pytest.raises(netbox_inventory.NetBoxInventoryError, match="Token authentication"):
        netbox_inventory.resolve_netbox_inventory(credential=object())
