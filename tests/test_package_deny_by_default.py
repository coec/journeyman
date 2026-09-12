from types import SimpleNamespace

import pytest

from app.auth import can_launch_package
from app.models import ProjectPackage
from app.models.project_package import (
    CONFIGURABLE_PACKAGE_ACCESS_MODES,
    PACKAGE_ACCESS_RESTRICTED,
)


def test_authenticated_access_mode_is_legacy_persistence_only():
    package = ProjectPackage(
        name="Legacy open Package",
        access_mode="authenticated",
    )

    assert package.access_mode == "authenticated"
    assert (
        "authenticated"
        not in CONFIGURABLE_PACKAGE_ACCESS_MODES
    )


def test_legacy_authenticated_value_fails_closed_at_runtime():
    package = SimpleNamespace(
        enabled=True,
        project=SimpleNamespace(enabled=True),
        access_mode="authenticated",
        permissions=[],
    )

    assert not can_launch_package(
        package,
        username="plain.user",
        group_names=(),
        is_admin=False,
    )


def test_restricted_package_without_grants_is_denied():
    package = SimpleNamespace(
        enabled=True,
        project=SimpleNamespace(enabled=True),
        access_mode=PACKAGE_ACCESS_RESTRICTED,
        permissions=[],
    )

    assert not can_launch_package(
        package,
        username="plain.user",
        group_names=(),
        is_admin=False,
    )
