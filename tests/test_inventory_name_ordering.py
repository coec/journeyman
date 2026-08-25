from app import db
from app.models import Inventory
from app.services.name_ordering import (
    RESERVED_NAME_ERROR,
    is_reserved_name,
    reserved_name_ordering,
    reserved_name_sort_key,
    reserved_name_validation_error,
)


def _inventory(name):
    return Inventory(
        name=name,
        inventory_type="static",
        enabled=True,
        config_json="{}",
    )


def test_python_name_sort_puts_reserved_zz_names_last():
    names = [
        "ZZ - Builtin B",
        "Beta",
        "alpha",
        "zz - Builtin A",
    ]

    assert sorted(names, key=reserved_name_sort_key) == [
        "alpha",
        "Beta",
        "zz - Builtin A",
        "ZZ - Builtin B",
    ]


def test_sql_inventory_order_puts_reserved_zz_names_last(app):
    with app.app_context():
        db.session.add_all([
            _inventory("ZZ - Builtin B"),
            _inventory("Beta"),
            _inventory("alpha"),
            _inventory("zz - Builtin A"),
        ])
        db.session.commit()

        names = [
            inventory.name
            for inventory in (
                Inventory.query
                .order_by(*reserved_name_ordering(Inventory.name))
                .all()
            )
        ]

        assert names == [
            "alpha",
            "Beta",
            "zz - Builtin A",
            "ZZ - Builtin B",
        ]


def test_reserved_zz_prefix_is_case_insensitive_and_trimmed():
    assert is_reserved_name("ZZ - Builtin")
    assert is_reserved_name("zz - builtin")
    assert is_reserved_name("  ZZ - Builtin")
    assert not is_reserved_name("ZZ-Builtin")
    assert not is_reserved_name("Production ZZ - Builtin")


def test_reserved_name_validation_uses_standard_error():
    assert (
        reserved_name_validation_error("ZZ - Builtin")
        == RESERVED_NAME_ERROR
    )
    assert reserved_name_validation_error("Normal inventory") is None


def test_reserved_name_validation_allows_existing_builtin_name_unchanged():
    assert (
        reserved_name_validation_error(
            "ZZ - Builtin",
            existing_name="ZZ - Builtin",
        )
        is None
    )
    assert (
        reserved_name_validation_error(
            "  zz - builtin  ",
            existing_name="ZZ - Builtin",
        )
        is None
    )


def test_reserved_name_validation_rejects_rename_to_reserved_name():
    assert reserved_name_validation_error(
        "ZZ - Builtin",
        existing_name="Normal object",
    )
    assert reserved_name_validation_error(
        "ZZ - Different builtin",
        existing_name="ZZ - Builtin",
    )
