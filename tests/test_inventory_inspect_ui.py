import json

from app import db
from app.models import Inventory
from tests.checks import (
    assert_output_contains,
    assert_output_equal,
    assert_output_excludes,
)


def _static_inventory(name, content):
    return Inventory(
        name=name,
        inventory_type="static",
        enabled=True,
        config_json=json.dumps({"content": content}),
        status="ready",
    )


def test_static_inventory_uses_inspect_action_without_refresh(client, app):
    with app.app_context():
        inventory = _static_inventory(
            "Static Inspect Test",
            """---
all:
  hosts:
    host01:
      foo: bar
""",
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
        purpose="Verify the administrator can render the Inventory list.",
    )
    html = response.data.decode("utf-8")

    assert_output_contains(
        html,
        "Static Inspect Test",
        purpose="Verify the static Inventory appears in the rendered list.",
    )
    assert_output_contains(
        html,
        'class="action-menu"',
        purpose="Verify the Inventory row exposes its action menu.",
    )
    assert_output_contains(
        html,
        '/inventories/{}/preview'.format(inventory_id),
        purpose="Verify a static Inventory exposes Inspect/Preview.",
    )
    assert_output_excludes(
        html,
        '/inventories/{}/refresh'.format(inventory_id),
        purpose="Verify a static Inventory does not expose a meaningless Refresh action.",
    )


def test_composite_inspect_shows_sources_and_merged_hostvars(client, app):
    with app.app_context():
        satellite_like = _static_inventory(
            "Satellite Source",
            """---
all:
  hosts:
    shared01:
      ansible_host: 192.0.2.10
      satellite_value: from-satellite
""",
        )
        zabbix_like = _static_inventory(
            "Zabbix Source",
            """---
all:
  hosts:
    shared01:
      ansible_host: 192.0.2.10
      zabbix_value: from-zabbix
""",
        )
        db.session.add_all([satellite_like, zabbix_like])
        db.session.flush()

        composite = Inventory(
            name="Composite Inspect Test",
            inventory_type="composite",
            enabled=True,
            config_json=json.dumps({
                "source_inventory_ids": [
                    satellite_like.id,
                    zabbix_like.id,
                ],
            }),
            status="ready",
        )
        db.session.add(composite)
        db.session.commit()
        composite_id = composite.id

    response = client.get(
        "/inventories/{}/preview?host=shared01".format(
            composite_id
        ),
        headers={"X-Test-Username": "admin"},
    )

    assert_output_equal(
        response.status_code,
        200,
        purpose="Verify the composite Inventory inspection page renders successfully.",
    )
    html = response.data.decode("utf-8")

    expected_output = (
        "Inspect Inventory",
        "Satellite Source",
        "Zabbix Source",
        "Merged Result",
        "satellite_value",
        "from-satellite",
        "zabbix_value",
        "from-zabbix",
        "Raw Resolved Inventory",
    )
    for expected in expected_output:
        assert_output_contains(
            html,
            expected,
            purpose=(
                "Verify composite inspection documents both source Inventories, "
                "their merged host variables, and the raw resolved result."
            ),
        )

    copy_controls = (
        'data-copy-target="merged-result-content"',
        'aria-label="Copy merged result"',
        'data-copy-target="raw-resolved-inventory-content"',
        'aria-label="Copy raw resolved inventory"',
    )
    for expected in copy_controls:
        assert_output_contains(
            html,
            expected,
            purpose=(
                "Verify inventory inspection exposes copy controls that target only "
                "the merged or raw inventory result text."
            ),
        )

    assert_output_excludes(
        html,
        "navigator.clipboard.writeText",
        purpose=(
            "Verify inventory inspection does not embed inline copy JavaScript that "
            "would be rejected by Journeyman's Content Security Policy."
        ),
    )


def test_inventory_copy_handler_is_loaded_from_static_javascript():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    javascript = (root / "app" / "static" / "js" / "journeyman.js").read_text(
        encoding="utf-8"
    )

    expected = (
        "initInventoryCopyButtons()",
        'document.querySelectorAll("button[data-copy-target]")',
        "navigator.clipboard.writeText(text)",
        'document.execCommand("copy")',
        'document.createElement("textarea")',
        "Journeyman.initInventoryCopyButtons();",
    )
    for value in expected:
        assert value in javascript
