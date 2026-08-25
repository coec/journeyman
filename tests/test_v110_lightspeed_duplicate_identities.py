"""Red Hat Lightspeed inventory duplicate identity diagnostics."""

from app.services import lightspeed_inventory


class DummyCredential:
    pass


def _record(record_id, hostname):
    return {
        "id": record_id,
        "fqdn": hostname,
        "display_name": hostname,
    }


def test_lightspeed_preserves_duplicate_source_records(monkeypatch):
    records = [
        _record("one", "tstsrv01.example.com"),
        _record("two", "tstsrv01.example.com"),
        _record("three", "tstsrv01.example.com"),
        _record("four", "tstsrv01.example.com"),
        _record("five", "other.example.com"),
    ]

    def fake_get_json(*args, **kwargs):
        return {
            "total": len(records),
            "count": len(records),
            "page": 1,
            "per_page": 100,
            "results": records,
        }

    monkeypatch.setattr(
        lightspeed_inventory,
        "get_json",
        fake_get_json,
    )

    resolved = lightspeed_inventory.resolve_lightspeed_inventory(
        credential=DummyCredential()
    )

    hostvars = resolved["_meta"]["hostvars"]
    diagnostics = resolved["_meta"][
        "journeyman_provider_diagnostics"
    ]

    assert len(hostvars) == 2
    assert hostvars["tstsrv01.example.com"]["redhat_lightspeed"]["id"] == "one"
    assert [
        record["id"]
        for record in hostvars["tstsrv01.example.com"][
            "redhat_lightspeed_source_records"
        ]
    ] == ["one", "two", "three", "four"]
    assert (
        hostvars["tstsrv01.example.com"][
            "redhat_lightspeed_duplicate_source_count"
        ]
        == 3
    )

    assert diagnostics["source_records"] == 5
    assert diagnostics["resolved_hosts"] == 2
    assert diagnostics["duplicate_source_records"] == 3
    assert diagnostics["duplicate_identities"] == {
        "tstsrv01.example.com": {
            "source_records": 4,
            "duplicate_source_records": 3,
            "record_ids": ["one", "two", "three", "four"],
        }
    }
