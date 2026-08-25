"""Discover Package-backed values needed for ad-hoc Inventory inspection."""

import re

from app import db
from app.models import Inventory, ProjectPackage
from app.services.inventory_resolver import inventory_config


_BINDING_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def _binding_names_in_value(value):
    names = set()
    if isinstance(value, dict):
        for item in value.values():
            names.update(_binding_names_in_value(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            names.update(_binding_names_in_value(item))
    elif isinstance(value, str):
        names.update(_BINDING_PATTERN.findall(value))
    return names


def inventory_binding_names(inventory, *, visited=None):
    """Return binding names used anywhere in an Inventory dependency tree."""

    visited = set(visited or ())
    if inventory.id in visited:
        return set()
    visited.add(inventory.id)

    config = inventory_config(inventory)
    names = _binding_names_in_value(config)

    source_ids = []
    if inventory.inventory_type == "filtered":
        source_ids = [config.get("source_inventory_id")]
    elif inventory.inventory_type == "composite":
        source_ids = config.get("source_inventory_ids", [])
        if not isinstance(source_ids, list):
            source_ids = []

    for source_id in source_ids:
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            continue
        source = db.session.get(Inventory, source_id)
        if source is not None:
            names.update(inventory_binding_names(source, visited=visited))

    return names


def _package_uses_inventory(package, inventory_id):
    project = package.project
    if project is None:
        return False
    if project.inventory_id == inventory_id:
        return True
    return any(step.inventory_id == inventory_id for step in project.steps)


def packages_for_inventory_bindings(inventory, binding_names):
    """Return enabled Packages that can provide every requested binding."""

    required = set(binding_names)
    packages = []

    for package in (
        ProjectPackage.query
        .filter(ProjectPackage.enabled.is_(True))
        .order_by(ProjectPackage.name.asc())
        .all()
    ):
        if not _package_uses_inventory(package, inventory.id):
            continue

        provided = {
            item.inventory_binding_name or item.variable_name
            for item in package.inputs
            if item.bind_to_inventory
        }
        if required.issubset(provided):
            packages.append(package)

    return packages
