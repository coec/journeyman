from datetime import datetime, timezone

import pytest

from app import db
from app.models import Runner
from app.services.inventory_runner_routing import (
    InventoryRunnerRoutingError,
    derive_inventory_runner_routing,
)


def _inventory(hostvars):
    return {
        1: {
            "all": {
                "hosts": list(hostvars),
                "children": [],
            },
            "_meta": {
                "hostvars": hostvars,
            },
        }
    }


def test_inventory_routing_keeps_localhost_on_builtin_runner(app):
    with app.app_context():
        routing = derive_inventory_runner_routing(
            _inventory({
                "localhost": {
                    "ansible_connection": "local",
                },
            })
        )

        assert routing["dispatch_target"] == "local"
        assert routing["required_runner_id"] is None
        assert routing["required_runner_site"] == ""


def test_inventory_routing_uses_common_site(app):
    with app.app_context():
        routing = derive_inventory_runner_routing(
            _inventory({
                "host01": {"journeyman_site": "site-a"},
                "host02": {"journeyman_site": "site-a"},
            })
        )

        assert routing["dispatch_target"] == "remote"
        assert routing["required_runner_id"] is None
        assert routing["required_runner_site"] == "site-a"


def test_inventory_routing_uses_specific_registered_runner(app):
    with app.app_context():
        runner = Runner(
            name="site-a-runner-01",
            runner_uuid="55555555-5555-5555-5555-555555555555",
            enabled=True,
            is_local=False,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        routing = derive_inventory_runner_routing(
            _inventory({
                "host01": {
                    "journeyman_runner": "site-a-runner-01",
                },
                "host02": {
                    "journeyman_runner": "site-a-runner-01",
                },
            })
        )

        assert routing["dispatch_target"] == "remote"
        assert routing["required_runner_id"] == runner.id
        assert routing["required_runner_site"] == ""


def test_inventory_routing_rejects_mixed_sites(app):
    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="multiple Journeyman sites",
        ):
            derive_inventory_runner_routing(
                _inventory({
                    "host01": {"journeyman_site": "site-a"},
                    "host02": {"journeyman_site": "site-b"},
                })
            )


def test_inventory_routing_rejects_missing_metadata(app):
    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="requires journeyman_site or journeyman_runner",
        ):
            derive_inventory_runner_routing(
                _inventory({
                    "host01": {"journeyman_site": "site-a"},
                    "host02": {},
                })
            )


def test_inventory_routing_rejects_local_and_remote_mix(app):
    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="cannot mix localhost targets with remote targets",
        ):
            derive_inventory_runner_routing(
                _inventory({
                    "localhost": {"ansible_connection": "local"},
                    "host01": {"journeyman_site": "site-a"},
                })
            )


def test_inventory_runner_override_accepts_registered_hostname(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        runner = Runner(
            name="runner-site-a",
            hostname="runner01.example.com",
            runner_uuid="66666666-6666-6666-6666-666666666666",
            enabled=True,
            is_local=False,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        assignments = validate_inventory_runner_overrides(
            _inventory({
                "host01": {
                    "journeyman_runner": "runner01.example.com",
                },
            })
        )

        assert assignments["host01"].id == runner.id


def test_inventory_runner_override_rejects_unregistered_hostname(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="no enabled registered remote runner",
        ):
            validate_inventory_runner_overrides(
                _inventory({
                    "host01": {
                        "journeyman_runner": "missing-runner.example.com",
                    },
                })
            )


def test_foreman_parameter_is_used_as_runner_override(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        runner = Runner(
            name="runner01",
            hostname="runner01.example.com",
            runner_uuid="88888888-8888-8888-8888-888888888888",
            enabled=True,
            is_local=False,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        assignments = validate_inventory_runner_overrides(
            _inventory({
                "app01.example.com.com": {
                    "foreman_params": {
                        "journeyman_runner":
                            "runner01.example.com",
                    },
                },
            })
        )

        assert assignments[
            "app01.example.com.com"
        ].id == runner.id


def test_foreman_parameter_rejects_unregistered_runner(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="no enabled registered remote runner",
        ):
            validate_inventory_runner_overrides(
                _inventory({
                    "host01": {
                        "foreman_params": {
                            "journeyman_runner":
                                "missing-runner.example.com",
                        },
                    },
                })
            )


def test_conflicting_direct_and_foreman_runner_values_are_rejected(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="conflicting journeyman_runner values",
        ):
            validate_inventory_runner_overrides(
                _inventory({
                    "host01": {
                        "journeyman_runner": "runner01.example.com",
                        "foreman_params": {
                            "journeyman_runner": "runner02.example.com",
                        },
                    },
                })
            )


def test_zabbix_runner_tag_is_used_as_runner_override(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        runner = Runner(
            name="runner-site-b",
            hostname="runner02.example.com",
            runner_uuid="77777777-7777-7777-7777-777777777777",
            enabled=True,
            is_local=False,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        assignments = validate_inventory_runner_overrides(
            _inventory({
                "host01": {
                    "zabbix": {
                        "tags_by_name": {
                            "journeyman_runner": ["runner02.example.com"],
                        },
                    },
                },
            })
        )

        assert assignments["host01"].id == runner.id


def test_zabbix_runner_tag_rejects_multiple_values(app):
    from app.services.inventory_runner_routing import (
        validate_inventory_runner_overrides,
    )

    with app.app_context():
        with pytest.raises(
            InventoryRunnerRoutingError,
            match="multiple different journeyman_runner tag values",
        ):
            validate_inventory_runner_overrides(
                _inventory({
                    "host01": {
                        "zabbix": {
                            "tags_by_name": {
                                "journeyman_runner": [
                                    "runner01.example.com",
                                    "runner02.example.com",
                                ],
                            },
                        },
                    },
                })
            )
