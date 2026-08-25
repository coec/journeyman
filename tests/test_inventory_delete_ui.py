from app import db
from app.models import Inventory
from tests.checks import (
    assert_output_contains,
    assert_output_equal,
)


def test_inventory_list_renders_delete_post_form(client, app):
    with app.app_context():
        inventory = Inventory(
            name="Disposable Inventory",
            inventory_type="static",
            enabled=True,
            config_json="{}",
        )
        db.session.add(inventory)
        db.session.commit()
        inventory_id = inventory.id

    response = client.get(
        "/inventories",
        headers={"X-Test-Username": "admin"},
    )

    assert_output_equal(
        response.status_code,
        200,
        purpose="Verify an administrator can render the Inventory list.",
    )

    html = response.data.decode("utf-8")
    assert_output_contains(
        html,
        'action="/inventories/{}/delete"'.format(inventory_id),
        purpose="Verify the Inventory row renders a delete action for that Inventory.",
    )
    assert_output_contains(
        html,
        'method="post"',
        purpose="Verify Inventory deletion is submitted as POST rather than GET.",
    )
    assert_output_contains(
        html,
        'name="csrf_token"',
        purpose="Verify the delete form carries CSRF protection.",
    )
    assert_output_contains(
        html,
        "Delete",
        purpose="Verify the administrator is shown the Delete action label.",
    )
