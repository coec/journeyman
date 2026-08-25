"""Inventory administration must not disclose configuration to normal users."""

import pytest

from app import db
from app.models import Credential, Inventory
from tests.checks import assert_output_excludes, assert_output_equal

pytestmark = pytest.mark.security


def test_inventory_list_is_admin_only_and_does_not_leak_metadata(client, app):
    """A normal user must receive 403 before inventory metadata is rendered."""
    with app.app_context():
        credential = Credential(
            name="Sensitive Satellite Credential",
            owner="admin",
            credential_type="satellite",
        )
        inventory = Inventory(
            name="Sensitive Production Inventory",
            inventory_type="satellite",
            endpoint="https://satellite.example.test",
            credential=credential,
            enabled=True,
            config_json='{"organization":"Production"}',
        )
        db.session.add(inventory)
        db.session.commit()

    response = client.get(
        "/inventories",
        headers={"X-Test-Username": "alice"},
    )

    assert_output_equal(
        response.status_code,
        403,
        purpose=(
            "Verify that a non-administrator cannot open the inventory "
            "administration page."
        ),
    )
    assert_output_excludes(
        response.data,
        "Sensitive Production Inventory",
        purpose="Verify that the rejected response does not leak inventory names.",
    )
    assert_output_excludes(
        response.data,
        "Sensitive Satellite Credential",
        purpose="Verify that the rejected response does not leak credential names.",
    )
