import os

from app.models import Inventory
from app.services.inventory_cache import inventory_cache_path, write_inventory_cache


def test_route_test_app_uses_isolated_instance_directory(app, tmp_path):
    """Pytest must never read or write Journeyman's live Flask instance data."""

    expected_instance_path = os.path.realpath(str(tmp_path / "instance"))
    assert os.path.realpath(app.instance_path) == expected_instance_path

    with app.app_context():
        inventory = Inventory(
            name="Test cache isolation",
            inventory_type="static",
            config_json="{}",
        )
        from app import db
        db.session.add(inventory)
        db.session.commit()

        write_inventory_cache(
            inventory,
            {"_meta": {"hostvars": {"test-host": {}}}},
        )
        cache_path = inventory_cache_path(inventory)

    assert os.path.commonpath(
        [os.path.realpath(cache_path), expected_instance_path]
    ) == expected_instance_path
    assert os.path.isfile(cache_path)


def test_route_test_runtime_paths_are_all_isolated(app, tmp_path):
    """Route tests must not point at installed Journeyman runtime data."""

    temporary_root = os.path.realpath(str(tmp_path))
    configured_paths = {
        "instance_path": app.instance_path,
        "repository_root": app.config["REPOSITORY_ROOT"],
        "log_root": app.config["LOG_ROOT"],
        "managed_environment_root": app.config["MANAGED_ENVIRONMENT_ROOT"],
        "credential_key_file": os.environ["JOURNEYMAN_CREDENTIAL_KEY_FILE"],
        "fallback_admin_hash": app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"],
        "tls_root": app.config["TLS_ROOT"],
        "tls_certificate": app.config["TLS_CERTIFICATE_PATH"],
        "tls_private_key": app.config["TLS_PRIVATE_KEY_PATH"],
    }

    for label, path in configured_paths.items():
        resolved = os.path.realpath(str(path))
        assert os.path.commonpath([resolved, temporary_root]) == temporary_root, (
            "{} escaped the pytest temporary root: {}".format(label, resolved)
        )

    database_uri = str(app.config["SQLALCHEMY_DATABASE_URI"])
    assert str(tmp_path / "route-tests.db") in database_uri
    assert "/opt/journeyman/instance" not in database_uri
    assert "/var/lib/journeyman" not in database_uri
