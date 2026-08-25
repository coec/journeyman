import json

from app import db
from app.models import Inventory
from tests.checks import (
    assert_output_contains,
    assert_output_equal,
    assert_output_excludes,
)


def test_inventory_actions_offer_clone(client, app):
    with app.app_context():
        inventory = Inventory(
            name="Clone source",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps({"content": "all:\n  hosts:\n    test01:\n"}),
        )
        db.session.add(inventory)
        db.session.commit()
        inventory_id = inventory.id

    response = client.get(
        "/inventories",
        headers={"X-Test-Username": "admin"},
    )

    assert_output_equal(response.status_code, 200, purpose="Inventory Clone response status")
    assert_output_contains(
        response.data.decode("utf-8"),
        '/inventories/new?clone={}'.format(inventory_id),
        purpose="Inventory Actions offers Clone through the normal new-inventory route.",
    )


def test_inventory_clone_prefills_new_form_but_clears_name(client, app):
    content = "all:\n  hosts:\n    clone-test01:\n"

    with app.app_context():
        inventory = Inventory(
            name="Clone source",
            inventory_type="static",
            enabled=False,
            config_json=json.dumps({
                "content": content,
                "append_domain": "example.com",
            }),
        )
        db.session.add(inventory)
        db.session.commit()
        inventory_id = inventory.id

    response = client.get(
        "/inventories/new?clone={}".format(inventory_id),
        headers={"X-Test-Username": "admin"},
    )

    assert_output_equal(response.status_code, 200, purpose="Inventory Clone response status")
    html = response.data.decode("utf-8")

    assert 'value="static"' in html
    static_option = html.split('value="static"', 1)[1].split("</option>", 1)[0]
    assert "selected" in static_option

    assert_output_contains(
        html,
        "clone-test01:",
        purpose="A cloned Static Inventory retains its provider-specific content.",
    )
    assert_output_contains(
        html,
        'value="example.com"',
        purpose="A cloned Inventory retains hostname-output configuration.",
    )
    assert_output_excludes(
        html,
        'name="name"\n                required\n                value="Clone source"',
        purpose="Clone does not carry the source Inventory name into the new object.",
    )

    with app.app_context():
        assert_output_equal(
            Inventory.query.count(),
            1,
            purpose="Opening Clone only pre-populates the create form and does not duplicate the Inventory server-side.",
        )


def test_inventory_clone_rejects_invalid_source_id(client):
    response = client.get(
        "/inventories/new?clone=not-an-id",
        headers={"X-Test-Username": "admin"},
    )

    assert_output_equal(response.status_code, 404, purpose="Invalid Inventory Clone source status")
