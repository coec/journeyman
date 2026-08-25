from app import db
from app.models import Inventory
from app.services.dispatch_progress import read_dispatch_progress


def test_inventory_refresh_form_uses_progress_modal(client, app):
    with app.app_context():
        inventory = Inventory(
            name="Slow source inventory",
            inventory_type="satellite",
            config_json="{}",
            enabled=True,
        )
        db.session.add(inventory)
        db.session.commit()
        inventory_id = inventory.id

    response = client.get("/inventories", headers={"X-Test-Username": "admin"})
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert 'action="/inventories/{}/refresh"'.format(inventory_id) in html
    assert "data-operation-progress" in html
    assert 'data-progress-title="Refreshing inventory"' in html
    assert 'data-progress-start-message="Starting inventory refresh"' in html
    assert 'data-progress-error-title="Inventory refresh failed"' in html


def test_inventory_refresh_reports_progress(client, app, monkeypatch):
    with app.app_context():
        inventory = Inventory(
            name="Progress inventory",
            inventory_type="satellite",
            config_json="{}",
            enabled=True,
        )
        db.session.add(inventory)
        db.session.commit()
        inventory_id = inventory.id

    from app.views import inventories as inventory_views

    monkeypatch.setattr(
        inventory_views,
        "refresh_inventory",
        lambda inventory: {"_meta": {"hostvars": {"host01": {}}}},
    )

    progress_id = "12345678-1234-1234-1234-123456789abc"
    response = client.post(
        "/inventories/{}/refresh".format(inventory_id),
        headers={
            "X-Test-Username": "admin",
            "X-Journeyman-Dispatch-Progress": progress_id,
            "X-Requested-With": "JourneymanDispatchProgress",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        progress = read_dispatch_progress(progress_id)
    assert progress is not None
    assert progress["state"] == "done"
    assert progress["phase"] == "complete"
    assert "1 host" in progress["message"]
