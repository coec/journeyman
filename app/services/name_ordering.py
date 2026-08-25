"""Shared ordering helpers for Journeyman object names."""

from sqlalchemy import case, func


RESERVED_NAME_PREFIX = "zz - "
RESERVED_NAME_ERROR = (
    'Names beginning with "ZZ - " are reserved for Journeyman built-in objects.'
)


def is_reserved_name(name):
    """Return whether a user-supplied name uses Journeyman's reserved prefix."""

    return str(name or "").strip().lower().startswith(
        RESERVED_NAME_PREFIX
    )


def reserved_name_validation_error(name, *, existing_name=None):
    """Return the standard validation error for an invalid reserved name.

    New user-created objects may not use Journeyman's reserved ``ZZ - ``
    namespace.  Existing built-in objects may, however, be edited while
    retaining their existing reserved name.  Renaming any object to a new
    reserved name remains prohibited.
    """

    if not is_reserved_name(name):
        return None

    if (
        existing_name is not None
        and is_reserved_name(existing_name)
        and str(name).strip().lower() == str(existing_name).strip().lower()
    ):
        return None

    return RESERVED_NAME_ERROR



def reserved_name_sort_key(name):
    """
    Sort normal names first and Journeyman-reserved ``ZZ - `` names last.

    Ordering within each section is case-insensitive.
    """

    normalized = str(name or "").strip().lower()
    return (
        1 if normalized.startswith(RESERVED_NAME_PREFIX) else 0,
        normalized,
    )


def reserved_name_ordering(column):
    """
    Return SQLAlchemy ORDER BY expressions matching ``reserved_name_sort_key``.
    """

    normalized = func.lower(func.trim(column))

    return (
        case(
            (
                normalized.like("{}%".format(RESERVED_NAME_PREFIX)),
                1,
            ),
            else_=0,
        ).asc(),
        normalized.asc(),
    )
