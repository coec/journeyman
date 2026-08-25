from datetime import datetime, timezone

from app import db
from app.models import Inventory


def test_inventory_list_shows_last_refresh(client, app):
    refreshed_at = datetime(
        2026,
        8,
        7,
        7,
        15,
        42,
        tzinfo=timezone.utc,
    )

    with app.app_context():
        refreshed = Inventory(
            name="Recently Refreshed",
            inventory_type="static",
            enabled=True,
            config_json="{}",
            status="ok",
            last_sync_at=refreshed_at,
        )
        never = Inventory(
            name="Never Refreshed",
            inventory_type="static",
            enabled=True,
            config_json="{}",
            status="never_synced",
            last_sync_at=None,
        )
        db.session.add_all([refreshed, never])
        db.session.commit()

    response = client.get(
        "/inventories",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200

    html = response.data.decode("utf-8")

    assert "Last Refresh" in html
    assert 'class="utc-datetime"' in html
    assert 'data-utc="2026-08-07T07:15:42' in html
    assert "Never" in html
