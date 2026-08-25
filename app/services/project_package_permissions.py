import re

from app.models import ProjectPackagePermission
from app.models.project_package import (
    PACKAGE_PRINCIPAL_GROUP,
    PACKAGE_PRINCIPAL_USER,
    VALID_PACKAGE_PRINCIPAL_TYPES,
)


CONTROL_CHARACTER_PATTERN = re.compile(
    r"[\x00-\x1f\x7f]"
)


def _clean(value):
    return str(value or "").strip()


def _legacy_principal_key(principal_type, principal_name):
    return "legacy|{}|{}".format(
        _clean(principal_type),
        _clean(principal_name).casefold(),
    )


def _guid_principal_key(principal_type, object_guid):
    return "{}|{}".format(
        _clean(principal_type),
        _clean(object_guid).lower(),
    )


def package_permission_rows_from_request(form):
    rows = []

    for row_key in form.getlist(
        "package_permission_row"
    ):
        row_key = str(row_key)

        prefix = (
            "package_permission_{}_"
            .format(row_key)
        )

        rows.append(
            {
                "row_key": row_key,
                "principal_key": _clean(
                    form.get(
                        prefix + "principal_key"
                    )
                ),
                # Retained for compatibility with tests and any old
                # form submissions during the migration window.
                "principal_type": _clean(
                    form.get(
                        prefix + "principal_type"
                    )
                ),
                "principal_name": _clean(
                    form.get(
                        prefix + "principal_name"
                    )
                ),
            }
        )

    return rows


def package_permission_rows_for_form(package):
    rows = []

    for index, permission in enumerate(
        package.permissions,
        start=1,
    ):
        if permission.principal_object_guid:
            principal_key = _guid_principal_key(
                permission.principal_type,
                permission.principal_object_guid,
            )
        else:
            principal_key = _legacy_principal_key(
                permission.principal_type,
                permission.principal_name,
            )

        rows.append(
            {
                "row_key": str(index),
                "principal_key": principal_key,
                "principal_type": (
                    permission.principal_type
                ),
                "principal_name": (
                    permission.principal_name
                ),
                "principal_object_guid": (
                    permission.principal_object_guid
                    or ""
                ),
                "principal_dn": (
                    permission.principal_dn
                    or ""
                ),
            }
        )

    return rows


def _validate_legacy_row(
    row,
    row_number,
    errors,
):
    principal_type = _clean(
        row.get("principal_type")
    )

    principal_name = _clean(
        row.get("principal_name")
    )

    if (
        principal_type
        not in VALID_PACKAGE_PRINCIPAL_TYPES
    ):
        errors.append(
            "Permission {} has an invalid principal type."
            .format(row_number)
        )

    if not principal_name:
        errors.append(
            "Permission {} requires a user or group name."
            .format(row_number)
        )
    elif len(principal_name) > 255:
        errors.append(
            "Permission {} principal name exceeds "
            "255 characters."
            .format(row_number)
        )
    elif CONTROL_CHARACTER_PATTERN.search(
        principal_name
    ):
        errors.append(
            "Permission {} principal name contains "
            "control characters."
            .format(row_number)
        )

    return {
        "principal_type": principal_type,
        "principal_name": principal_name,
        "principal_object_guid": _clean(
            row.get("principal_object_guid")
        ) or None,
        "principal_dn": _clean(
            row.get("principal_dn")
        ),
    }


def validate_package_permission_rows(
    rows,
    allowed_principals=None,
):
    """
    Validate Package execute grants.

    When allowed_principals is supplied, every submitted value must be
    a canonical directory-backed User or registered Team selection.
    Omitting it retains legacy validation for lower-level unit tests and
    migration utilities; web routes always supply the directory map.
    """

    errors = []
    normalised_rows = []
    seen_principals = set()

    for row_number, row in enumerate(
        rows,
        start=1,
    ):
        if allowed_principals is None:
            normalised = _validate_legacy_row(
                row,
                row_number,
                errors,
            )
        else:
            principal_key = _clean(
                row.get("principal_key")
            )

            normalised = allowed_principals.get(
                principal_key
            )

            if normalised is None:
                errors.append(
                    "Permission {} must select an eligible "
                    "Active Directory user or registered Team."
                    .format(row_number)
                )
                normalised = {
                    "principal_type": "",
                    "principal_name": "",
                    "principal_object_guid": None,
                    "principal_dn": "",
                }
            else:
                normalised = dict(normalised)

        principal_type = normalised[
            "principal_type"
        ]
        principal_name = normalised[
            "principal_name"
        ]
        object_guid = normalised.get(
            "principal_object_guid"
        )

        principal_key = (
            principal_type,
            (
                str(object_guid).lower()
                if object_guid
                else principal_name.casefold()
            ),
        )

        if (
            principal_type
            in VALID_PACKAGE_PRINCIPAL_TYPES
            and principal_name
        ):
            if principal_key in seen_principals:
                errors.append(
                    "Permission {} duplicates an existing "
                    "{} permission for {}."
                    .format(
                        row_number,
                        principal_type,
                        principal_name,
                    )
                )
            else:
                seen_principals.add(
                    principal_key
                )

        normalised_rows.append(
            normalised
        )

    return (
        errors,
        normalised_rows,
    )


def apply_package_permission_rows(
    package,
    rows,
    session,
):
    existing_permissions = list(
        package.permissions
    )

    for permission in existing_permissions:
        session.delete(
            permission
        )

    if existing_permissions:
        session.flush()

    for row in rows:
        package.permissions.append(
            ProjectPackagePermission(
                principal_type=(
                    row["principal_type"]
                ),
                principal_name=(
                    row["principal_name"]
                ),
                principal_object_guid=(
                    row.get(
                        "principal_object_guid"
                    )
                ),
                principal_dn=(
                    row.get("principal_dn")
                    or ""
                ),
            )
        )


__all__ = [
    "PACKAGE_PRINCIPAL_GROUP",
    "PACKAGE_PRINCIPAL_USER",
    "apply_package_permission_rows",
    "package_permission_rows_for_form",
    "package_permission_rows_from_request",
    "validate_package_permission_rows",
]
