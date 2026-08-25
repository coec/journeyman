from pathlib import Path

import pytest

from app import db
from app.credential_types import CREDENTIAL_TYPE_URL
from app.models import Credential
from app.services.lightspeed_inventory import resolve_lightspeed_inventory
from app.services.netbox_inventory import resolve_netbox_inventory
from app.services.url_credentials import normalise_url_credential_data


def test_url_credential_normalises_bearer_defaults():
    data = normalise_url_credential_data(
        {"url": "https://api.example.org/", "auth_mode": "bearer", "token": "secret"},
        username="",
    )
    assert data["url"] == "https://api.example.org"
    assert data["token_prefix"] == "Bearer"


def test_url_credential_supports_oauth_client_credentials():
    data = normalise_url_credential_data(
        {
            "url": "https://console.redhat.com",
            "auth_mode": "oauth2_client_credentials",
            "password": "client-secret",
            "token_url": "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
            "scope": "openid api.iam.service_accounts",
        },
        username="client-id",
    )
    assert data["auth_mode"] == "oauth2_client_credentials"
    assert data["scope"] == "openid api.iam.service_accounts"


def test_netbox_inventory_maps_devices(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services import netbox_inventory

    monkeypatch.setattr(
        netbox_inventory,
        "url_credential_details",
        lambda credential: ("", {
            "url": "https://netbox.example.org",
            "auth_mode": "token",
            "token": "secret",
        }),
    )
    monkeypatch.setattr(
        netbox_inventory.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "_meta": {"hostvars": {
                    "rtr01.example.org": {
                        "ansible_host": "192.0.2.10",
                        "site": {"slug": "sydney"},
                        "role": {"slug": "router"},
                    }
                }},
                "all": {"children": ["ungrouped"]},
                "ungrouped": {"hosts": ["rtr01.example.org"]},
            }),
            stderr="",
        ),
    )
    result = resolve_netbox_inventory(credential=object())
    assert result["_meta"]["hostvars"]["rtr01.example.org"]["ansible_host"] == "192.0.2.10"

def test_lightspeed_inventory_prefers_fqdn(monkeypatch):
    monkeypatch.setattr(
        "app.services.lightspeed_inventory.get_json",
        lambda *args, **kwargs: {
            "results": [{"id": "abc", "fqdn": "rhel01.example.org", "display_name": "rhel01"}],
            "total": 1,
        },
    )
    result = resolve_lightspeed_inventory(credential=object())
    assert result["lightspeed_hosts"]["hosts"] == ["rhel01.example.org"]
    assert result["_meta"]["hostvars"]["rhel01.example.org"]["redhat_lightspeed"]["id"] == "abc"


def test_url_credential_type_can_be_persisted(app):
    with app.app_context():
        credential = Credential(
            name="NetBox API",
            owner="admin",
            credential_type=CREDENTIAL_TYPE_URL,
            username="",
        )
        credential.set_credential_data(
            {
                "url": "https://netbox.example.org",
                "auth_mode": "token",
                "token": "secret",
                "token_prefix": "Token",
            }
        )
        db.session.add(credential)
        db.session.commit()
        assert credential.get_credential_data()["url"] == "https://netbox.example.org"


def test_operator_tools_survive_roadmap_roll_forward():
    root = Path(__file__).resolve().parents[1]
    roadmap = (root / "ROADMAP.md").read_text(encoding="utf-8")

    # ROADMAP.md describes future work, so completed feature names and exact
    # release numbers must not be required to remain there forever.  Keep only
    # the release-status structure coupled to the operator artefact checks.
    assert "## Release status" in roadmap
    assert "Current:" in roadmap
    assert "Target:" in roadmap

    assert (root / "scripts" / "journeyman-smit").is_file()
    assert (root / "scripts" / "journeyman-postgresql-upgrade").is_file()
    assert (root / "docs" / "POSTGRESQL_UPGRADE.md").is_file()
