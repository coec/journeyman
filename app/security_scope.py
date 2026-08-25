"""
Common security-scope definitions used by Journeyman objects.

Scopes:

private
    Only the owner and administrators may use the object.

shared
    The owner, administrators, and explicitly authorised users or
    groups may use the object. Explicit sharing is not implemented yet,
    so shared currently behaves like private.

public
    Any authenticated Journeyman user may use the object.

Owning an object and using an object are separate from editing it.
Public or shared access does not automatically grant edit permission.
"""

SECURITY_SCOPE_PRIVATE = "private"
SECURITY_SCOPE_SHARED = "shared"
SECURITY_SCOPE_PUBLIC = "public"

VALID_SECURITY_SCOPES = frozenset(
    (
        SECURITY_SCOPE_PRIVATE,
        SECURITY_SCOPE_SHARED,
        SECURITY_SCOPE_PUBLIC,
    )
)

SECURITY_SCOPE_CHOICES = (
    (
        SECURITY_SCOPE_PRIVATE,
        "Private",
    ),
    (
        SECURITY_SCOPE_SHARED,
        "Shared",
    ),
    (
        SECURITY_SCOPE_PUBLIC,
        "Public",
    ),
)


def is_valid_security_scope(value):
    """
    Return True when value is a recognised security scope.
    """

    return value in VALID_SECURITY_SCOPES


def validate_security_scope(value):
    """
    Validate and return a security scope.

    Raise ValueError when the supplied value is invalid.
    """

    if not is_valid_security_scope(value):
        raise ValueError(
            "Invalid security scope: {!r}".format(value)
        )

    return value


def can_use_scoped_object(
    owner,
    security_scope,
    username,
    is_admin=False,
):
    """
    Return whether a user may use a security-scoped object.

    Shared currently behaves like private. Explicit user and group
    grants will be added later.
    """

    validate_security_scope(security_scope)

    if is_admin:
        return True

    if owner == username:
        return True

    if security_scope == SECURITY_SCOPE_PUBLIC:
        return True

    return False


def can_manage_scoped_object(
    owner,
    username,
    is_admin=False,
):
    """
    Return whether a user may edit or delete a scoped object.

    Sharing an object grants use, not management.
    """

    return is_admin or owner == username

def can_reveal_scoped_object_secret(
    owner,
    username,
):
    """
    Return whether a user may reveal an existing secret.

    Administrators are deliberately excluded. Only the owner may
    decrypt and display an existing secret.
    """

    return bool(username) and owner == username

