import json
from pathlib import Path

from app import db
from app.models import Credential, Inventory
from app.services import inventory_resolver
from app.services.inventory_resolver import (
    refresh_inventory,
    resolve_inventory,
)
from app.services.zabbix_inventory import (
    _canonical_inventory,
    _request_hosts,
    _request_icmp_items,
    _request_network_interface_items,
)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _zabbix_credential(name, token):
    credential = Credential(
        name=name,
        description="Zabbix API token",
        owner="admin",
        security_scope="private",
        credential_type="zabbix",
        username="",
    )
    credential.set_credential_data(
        {
            "token": token,
        }
    )
    return credential


def test_zabbix_host_get_uses_bearer_token_and_exact_tag(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "result": [],
                    "id": 1,
                }
            )

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(
        "app.services.zabbix_inventory.build_opener",
        fake_build_opener,
    )
    hosts = _request_hosts(
        endpoint="https://zabbix.example/zabbix",
        token="zbx-token-value",
        tag_name="automation",
        tag_value="journeyman",
        verify_tls=False,
        include_disabled=False,
        timeout=17,
    )

    assert hosts == []
    assert captured["timeout"] == 17

    request = captured["request"]
    assert request.full_url == (
        "https://zabbix.example/zabbix/api_jsonrpc.php"
    )
    assert request.get_header("Authorization") == (
        "Bearer zbx-token-value"
    )

    payload = json.loads(request.data.decode("utf-8"))
    assert payload["method"] == "host.get"
    assert payload["params"]["tags"] == [
        {
            "tag": "automation",
            "value": "journeyman",
            "operator": 1,
        }
    ]
    assert payload["params"]["filter"] == {
        "status": "0",
    }


def test_zabbix_icmp_item_request_is_limited_to_icmpping(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse({
                "jsonrpc": "2.0",
                "result": [],
                "id": 2,
            })

    monkeypatch.setattr(
        "app.services.zabbix_inventory.build_opener",
        lambda *handlers: FakeOpener(),
    )

    items = _request_icmp_items(
        endpoint="https://zabbix.example/zabbix",
        token="zbx-token-value",
        hostids=["10002", "10001", "10001"],
        verify_tls=False,
        timeout=19,
    )

    assert items == []
    assert captured["timeout"] == 19
    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert payload["method"] == "item.get"
    assert payload["params"]["hostids"] == ["10001", "10002"]
    assert payload["params"]["search"] == {"key_": "icmpping"}
    assert payload["params"]["sortfield"] == ["name", "key_"]
    assert payload["params"]["startSearch"] is True
    assert "lastvalue" in payload["params"]["output"]
    assert "lastclock" in payload["params"]["output"]



def test_zabbix_network_interface_item_request_is_limited_to_net_if(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse({
                "jsonrpc": "2.0",
                "result": [],
                "id": 3,
            })

    monkeypatch.setattr(
        "app.services.zabbix_inventory.build_opener",
        lambda *handlers: FakeOpener(),
    )

    items = _request_network_interface_items(
        endpoint="https://zabbix.example/zabbix",
        token="zbx-token-value",
        hostids=["10002", "10001", "10001"],
        verify_tls=False,
        timeout=21,
    )

    assert items == []
    assert captured["timeout"] == 21
    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert payload["method"] == "item.get"
    assert payload["params"]["hostids"] == ["10001", "10002"]
    assert payload["params"]["search"] == {"key_": "net.if."}
    assert payload["params"]["startSearch"] is True


def test_zabbix_hosts_become_canonical_ansible_inventory():
    inventory = _canonical_inventory(
        [
            {
                "hostid": "10001",
                "host": "app01",
                "name": "Site A App 01",
                "status": "0",
                "description": "Application node",
                "inventory_mode": "1",
                "monitored_by": "0",
                "proxyid": "0",
                "assigned_proxyid": "0",
                "interfaces": [
                    {
                        "interfaceid": "5",
                        "main": "1",
                        "type": "1",
                        "useip": "1",
                        "ip": "172.22.10.21",
                        "dns": "app01.example.test",
                        "port": "10050",
                        "available": "1",
                        "error": "",
                    }
                ],
                "hostgroups": [
                    {
                        "groupid": "42",
                        "name": "Site A/Linux",
                    }
                ],
                "tags": [
                    {
                        "tag": "automation",
                        "value": "journeyman",
                    },
                    {
                        "tag": "journeyman_site",
                        "value": "site-a",
                    },
                ],
                "inventory": {
                    "os": "Linux",
                },
                "parentTemplates": [
                    {
                        "templateid": "20001",
                        "host": "Template Module ICMP Ping",
                        "name": "ICMP Ping",
                    }
                ],
            }
        ],
        icmp_items=[
            {
                "itemid": "30001",
                "hostid": "10001",
                "name": "ICMP ping",
                "key_": "icmpping",
                "status": "0",
                "state": "0",
                "error": "",
                "lastvalue": "1",
                "lastclock": "1787081234",
                "units": "",
                "value_type": "3",
            }
        ],
        network_interface_items=[
            {
                "itemid": "40001",
                "hostid": "10001",
                "name": "Interface GigabitEthernet1/0/1(Uplink to core): Operational status",
                "key_": "net.if.status[ifOperStatus.10101]",
                "status": "0",
                "state": "0",
                "error": "",
                "lastvalue": "1",
                "lastclock": "1787081240",
                "units": "",
                "value_type": "3",
            },
            {
                "itemid": "40002",
                "hostid": "10001",
                "name": "Interface GigabitEthernet1/0/1(Uplink to core): Speed",
                "key_": "net.if.speed[ifHighSpeed.10101]",
                "status": "0",
                "state": "0",
                "error": "",
                "lastvalue": "1000",
                "lastclock": "1787081240",
                "units": "Mbps",
                "value_type": "3",
            },
        ],
    )

    assert inventory["zabbix_hosts"]["hosts"] == [
        "app01"
    ]
    assert inventory["zabbix_group_42"]["hosts"] == [
        "app01"
    ]
    assert (
        inventory["zabbix_group_42"]["vars"]["zabbix_group_name"]
        == "Site A/Linux"
    )

    hostvars = inventory["_meta"]["hostvars"]["app01"]
    assert hostvars["ansible_host"] == "app01"
    assert hostvars["zabbix"]["hostid"] == "10001"
    assert hostvars["zabbix"]["enabled"] is True
    assert hostvars["zabbix"]["tags_by_name"] == {
        "automation": ["journeyman"],
        "journeyman_site": ["site-a"],
    }
    assert hostvars["zabbix"]["interfaces"][0]["use_ip"] is True
    assert hostvars["zabbix"]["interfaces"][0]["ip"] == "172.22.10.21"
    assert hostvars["zabbix"]["templates"] == [
        {
            "templateid": "20001",
            "host": "Template Module ICMP Ping",
            "name": "ICMP Ping",
        }
    ]
    assert hostvars["zabbix"]["icmp"]["configured"] is True
    assert hostvars["zabbix"]["icmp"]["reachable"] is True
    assert hostvars["zabbix"]["icmp"]["last_value"] == "1"
    assert hostvars["zabbix"]["icmp"]["last_clock"] == "1787081234"
    assert hostvars["zabbix"]["network_interfaces"] == [
        {
            "index": "10101",
            "name": "GigabitEthernet1/0/1",
            "alias": "Uplink to core",
            "description": "",
            "oper_status": "1",
            "admin_status": "",
            "speed": "1000",
            "type": "",
            "mtu": "",
            "items": {
                "net.if.status[ifOperStatus.10101]": {
                    "itemid": "40001",
                    "name": "Interface GigabitEthernet1/0/1(Uplink to core): Operational status",
                    "key": "net.if.status[ifOperStatus.10101]",
                    "enabled": True,
                    "supported": True,
                    "error": "",
                    "last_value": "1",
                    "last_clock": "1787081240",
                    "units": "",
                    "value_type": "3",
                },
                "net.if.speed[ifHighSpeed.10101]": {
                    "itemid": "40002",
                    "name": "Interface GigabitEthernet1/0/1(Uplink to core): Speed",
                    "key": "net.if.speed[ifHighSpeed.10101]",
                    "enabled": True,
                    "supported": True,
                    "error": "",
                    "last_value": "1000",
                    "last_clock": "1787081240",
                    "units": "Mbps",
                    "value_type": "3",
                },
            },
        }
    ]


def test_zabbix_refresh_is_cached_for_normal_resolution(
    app,
    monkeypatch,
):
    with app.app_context():
        credential = _zabbix_credential(
            "Zabbix token",
            "secret-token",
        )
        db.session.add(credential)
        db.session.flush()

        inventory = Inventory(
            name="Zabbix Site A",
            inventory_type="zabbix",
            endpoint="https://zabbix.example/zabbix",
            credential_id=credential.id,
            verify_tls=True,
            enabled=True,
            config_json=json.dumps(
                {
                    "tag_name": "automation",
                    "tag_value": "journeyman",
                    "include_disabled": False,
                }
            ),
            status="never_synced",
        )
        db.session.add(inventory)
        db.session.commit()

        live_data = {
            "_meta": {
                "hostvars": {
                    "app01": {
                        "ansible_host": "172.22.10.21",
                    },
                },
            },
            "all": {
                "children": ["zabbix_hosts"],
            },
            "zabbix_hosts": {
                "hosts": ["app01"],
            },
        }

        monkeypatch.setattr(
            inventory_resolver,
            "_resolve_zabbix_inventory_live",
            lambda _inventory: live_data,
        )

        refreshed = refresh_inventory(inventory)
        assert refreshed == live_data

        monkeypatch.setattr(
            inventory_resolver,
            "_resolve_zabbix_inventory_live",
            lambda _inventory: (_ for _ in ()).throw(
                AssertionError(
                    "normal resolve contacted Zabbix"
                )
            ),
        )

        resolved = resolve_inventory(inventory)
        assert resolved == live_data


def test_admin_can_create_zabbix_inventory(client, app):
    with app.app_context():
        credential = _zabbix_credential(
            "Zabbix create token",
            "create-token",
        )
        db.session.add(credential)
        db.session.commit()
        credential_id = credential.id

    response = client.post(
        "/inventories/new",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Zabbix PROD",
            "inventory_type": "zabbix",
            "zabbix_credential_id": str(credential_id),
            "zabbix_endpoint": (
                "https://zabbix.example/zabbix/"
            ),
            "zabbix_tag_name": "automation",
            "zabbix_tag_value": "journeyman",
            "zabbix_verify_tls": "on",
            "enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        inventory = Inventory.query.filter_by(
            name="Zabbix PROD"
        ).one()

        assert inventory.inventory_type == "zabbix"
        assert inventory.credential_id == credential_id
        assert inventory.endpoint == (
            "https://zabbix.example/zabbix"
        )
        assert inventory.verify_tls is True

        config = json.loads(inventory.config_json)
        assert config == {
            "include_disabled": False,
            "tag_name": "automation",
            "tag_value": "journeyman",
        }


def test_editing_zabbix_inventory_updates_connection_settings(
    client,
    app,
):
    with app.app_context():
        old_credential = _zabbix_credential(
            "Old Zabbix token",
            "old-token",
        )
        new_credential = _zabbix_credential(
            "New Zabbix token",
            "new-token",
        )
        db.session.add_all(
            [
                old_credential,
                new_credential,
            ]
        )
        db.session.flush()

        inventory = Inventory(
            name="Editable Zabbix",
            inventory_type="zabbix",
            endpoint="https://old.example/zabbix",
            credential_id=old_credential.id,
            verify_tls=True,
            enabled=True,
            config_json=json.dumps(
                {
                    "tag_name": "automation",
                    "tag_value": "journeyman",
                    "include_disabled": False,
                }
            ),
            status="ok",
        )
        db.session.add(inventory)
        db.session.commit()

        inventory_id = inventory.id
        new_credential_id = new_credential.id

    response = client.post(
        "/inventories/{}/edit".format(inventory_id),
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Editable Zabbix",
            "inventory_type": "zabbix",
            "zabbix_credential_id": str(new_credential_id),
            "zabbix_endpoint": "https://new.example/zabbix",
            "zabbix_tag_name": "journeyman",
            "zabbix_tag_value": "yes",
            "enabled": "on",
            # zabbix_verify_tls deliberately omitted.
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        inventory = db.session.get(
            Inventory,
            inventory_id,
        )

        assert inventory.credential_id == new_credential_id
        assert inventory.endpoint == (
            "https://new.example/zabbix"
        )
        assert inventory.verify_tls is False
        assert inventory.status == "never_synced"
        assert inventory.last_sync_at is None

        config = json.loads(inventory.config_json)
        assert config["tag_name"] == "journeyman"
        assert config["tag_value"] == "yes"
