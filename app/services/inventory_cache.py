"""
Secure local storage for resolved source inventories.

Cached inventories can contain sensitive host variables. Files are
stored beneath Flask's instance directory with restrictive permissions.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from flask import current_app


class InventoryCacheError(Exception):
    """
    Raised when cached inventory data cannot be stored or loaded.
    """


class InventoryCacheMissingError(InventoryCacheError):
    """
    Raised when an inventory has not yet been cached.
    """


def _cache_directory():
    path = os.path.join(
        current_app.instance_path,
        "inventory_cache",
    )

    try:
        os.makedirs(
            path,
            mode=0o700,
            exist_ok=True,
        )

        os.chmod(
            path,
            0o700,
        )

    except OSError as exc:
        raise InventoryCacheError(
            "Unable to prepare the inventory cache directory."
        ) from exc

    return path


def inventory_cache_path(inventory):
    """
    Return the cache path for one inventory.
    """

    return os.path.join(
        _cache_directory(),
        "{}.json".format(inventory.id),
    )


def _validate_inventory_data(inventory_data):
    """
    Validate basic canonical inventory structure.
    """

    if not isinstance(inventory_data, dict):
        raise InventoryCacheError(
            "Cached inventory data must be an object."
        )

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars")
    )

    if not isinstance(hostvars, dict):
        raise InventoryCacheError(
            "Cached inventory data has no hostvars mapping."
        )

    return inventory_data


def write_inventory_cache(
    inventory,
    inventory_data,
):
    """
    Atomically store canonical inventory JSON.
    """

    _validate_inventory_data(
        inventory_data
    )

    cache_path = inventory_cache_path(
        inventory
    )

    cache_directory = os.path.dirname(
        cache_path
    )

    temporary_path = None

    try:
        file_descriptor, temporary_path = (
            tempfile.mkstemp(
                prefix=".{}-".format(
                    inventory.id
                ),
                suffix=".tmp",
                dir=cache_directory,
            )
        )

        os.fchmod(
            file_descriptor,
            0o600,
        )

        with os.fdopen(
            file_descriptor,
            mode="w",
            encoding="utf-8",
        ) as cache_file:
            json.dump(
                inventory_data,
                cache_file,
                ensure_ascii=False,
                separators=(",", ":"),
            )

            cache_file.write("\n")
            cache_file.flush()
            os.fsync(
                cache_file.fileno()
            )

        os.replace(
            temporary_path,
            cache_path,
        )

        temporary_path = None

        os.chmod(
            cache_path,
            0o600,
        )

    except (OSError, TypeError, ValueError) as exc:
        raise InventoryCacheError(
            'Unable to cache inventory "{}".'
            .format(inventory.name)
        ) from exc

    finally:
        if temporary_path:
            try:
                os.remove(
                    temporary_path
                )
            except OSError:
                pass


def load_inventory_cache(inventory):
    """
    Load canonical inventory JSON from the local cache.
    """

    cache_path = inventory_cache_path(
        inventory
    )

    if not os.path.isfile(cache_path):
        raise InventoryCacheMissingError(
            'Inventory "{}" has not been refreshed yet.'
            .format(inventory.name)
        )

    try:
        with open(
            cache_path,
            mode="r",
            encoding="utf-8",
        ) as cache_file:
            inventory_data = json.load(
                cache_file
            )

    except (OSError, ValueError) as exc:
        raise InventoryCacheError(
            'Unable to read the cached inventory for "{}".'
            .format(inventory.name)
        ) from exc

    return _validate_inventory_data(
        inventory_data
    )


def delete_inventory_cache(inventory):
    """
    Remove one cached source inventory, if present.
    """

    cache_path = inventory_cache_path(
        inventory
    )

    try:
        os.remove(
            cache_path
        )

    except FileNotFoundError:
        return

    except OSError as exc:
        raise InventoryCacheError(
            'Unable to invalidate the cached inventory for "{}".'
            .format(inventory.name)
        ) from exc


def inventory_host_count(inventory_data):
    """
    Return the host count from canonical inventory JSON.
    """

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars", {})
    )

    if not isinstance(hostvars, dict):
        return 0

    return len(hostvars)


def purge_expired_inventory_caches(*, max_age_seconds, now=None, dry_run=False):
    """Remove cached inventory files older than the configured maximum age."""

    if int(max_age_seconds) <= 0:
        return []

    now = now or datetime.now(timezone.utc)
    cutoff_timestamp = now.timestamp() - int(max_age_seconds)
    directory = _cache_directory()
    removed = []

    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        raise InventoryCacheError("Unable to inspect the inventory cache directory.") from exc

    for entry in entries:
        if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff_timestamp:
                continue
            removed.append(entry.path)
            if not dry_run:
                os.remove(entry.path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InventoryCacheError("Unable to purge an expired inventory cache file.") from exc

    return removed
