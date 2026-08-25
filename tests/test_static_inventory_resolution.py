import json

from app import db
from app.models import Inventory
from app.services.inventory_cache import (
    delete_inventory_cache,
)
from app.services.inventory_resolver import (
    resolve_inventory,
)


STATIC_CONTENT = """---
all:
  hosts:
    localhost:
      ansible_connection: local
"""


def test_static_inventory_resolves_without_refresh(app):
    with app.app_context():
        inventory = Inventory(
            name="inv-localhost",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps(
                {
                    "content": STATIC_CONTENT,
                }
            ),
            status="never_synced",
        )
        db.session.add(inventory)
        db.session.commit()

        delete_inventory_cache(inventory)

        resolved = resolve_inventory(inventory)

        assert "localhost" in resolved["_meta"]["hostvars"]
        assert (
            resolved["_meta"]["hostvars"]["localhost"][
                "ansible_connection"
            ]
            == "local"
        )


def test_static_inventory_edit_is_immediately_visible_without_refresh(
    app,
):
    with app.app_context():
        inventory = Inventory(
            name="editable-static",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps(
                {
                    "content": STATIC_CONTENT,
                }
            ),
            status="never_synced",
        )
        db.session.add(inventory)
        db.session.commit()

        delete_inventory_cache(inventory)

        inventory.config_json = json.dumps(
            {
                "content": """---
all:
  hosts:
    newhost:
      ansible_host: 192.0.2.10
""",
            }
        )
        db.session.commit()

        resolved = resolve_inventory(inventory)

        assert "newhost" in resolved["_meta"]["hostvars"]
        assert (
            resolved["_meta"]["hostvars"]["newhost"]["ansible_host"]
            == "192.0.2.10"
        )


def test_static_inventory_flattens_host_vars_mapping(app):
    with app.app_context():
        inventory = Inventory(
            name="nested-host-vars",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps(
                {
                    "content": """---
all:
  hosts:
    kunmon02:
      vars:
        journeyman_runner: kunrun01
        foo: bar
        test: true
        mylist:
          - item1
          - item2
""",
                }
            ),
            status="never_synced",
        )
        db.session.add(inventory)
        db.session.commit()

        delete_inventory_cache(inventory)

        resolved = resolve_inventory(inventory)
        hostvars = resolved["_meta"]["hostvars"]["kunmon02"]

        assert hostvars["journeyman_runner"] == "kunrun01"
        assert hostvars["foo"] == "bar"
        assert hostvars["test"] is True
        assert hostvars["mylist"] == ["item1", "item2"]
        assert "vars" not in hostvars


def test_static_inventory_direct_host_var_wins_over_nested_vars(app):
    with app.app_context():
        inventory = Inventory(
            name="host-var-precedence",
            inventory_type="static",
            enabled=True,
            config_json=json.dumps(
                {
                    "content": """---
all:
  hosts:
    kunmon02:
      journeyman_runner: kunrun02
      vars:
        journeyman_runner: kunrun01
""",
                }
            ),
            status="never_synced",
        )
        db.session.add(inventory)
        db.session.commit()

        delete_inventory_cache(inventory)

        resolved = resolve_inventory(inventory)
        hostvars = resolved["_meta"]["hostvars"]["kunmon02"]

        assert hostvars["journeyman_runner"] == "kunrun02"
        assert "vars" not in hostvars
