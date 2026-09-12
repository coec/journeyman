"""Journeyman-local authorization foundations.

v2.0 groundwork deliberately keeps these records separate from the current
LDAP-derived runtime authorization decision.  The cut-over to local authority
is a later migration step.
"""

from datetime import datetime, timezone

from app import db
from app.models import AuthorizationRight, AuthorizationRole, UserAccount

ROLE_ADMIN = "Admin"
ROLE_AUTOMATION_ADMIN = "Automation Admin"
ROLE_RESOURCE_ADMIN = "Resource Admin"
ROLE_USER = "User"
ROLE_AUDITOR = "Auditor"

RIGHT_REVIEWER = "Reviewer"
RIGHT_APPROVER = "Approver"

BUILTIN_ROLES = {
    ROLE_ADMIN: "Manage Journeyman itself, including users, teams, and system configuration.",
    ROLE_AUTOMATION_ADMIN: "Manage Projects, Packages, Signals, Sources, and Reactors.",
    ROLE_RESOURCE_ADMIN: "Manage repositories, credentials, inventories, environments, and other execution resources.",
    ROLE_USER: "Launch Packages assigned directly or inherited through team membership.",
    ROLE_AUDITOR: "Read-only access to Journeyman configuration, settings, jobs, approvals, and audit information.",
}

ROLES_IMPLYING_USER = {
    ROLE_ADMIN,
    ROLE_AUTOMATION_ADMIN,
    ROLE_RESOURCE_ADMIN,
}


BUILTIN_RIGHTS = {
    RIGHT_REVIEWER: "Perform technical reviews of submitted automation.",
    RIGHT_APPROVER: "Perform management approval of reviewed automation.",
}


def utcnow():
    return datetime.now(timezone.utc)


def ensure_builtin_authorization_definitions():
    """Ensure the built-in role/right catalogue exists and return it."""

    roles = {}
    rights = {}

    for name, description in BUILTIN_ROLES.items():
        row = AuthorizationRole.query.filter_by(name=name).one_or_none()
        if row is None:
            row = AuthorizationRole(name=name, description=description, builtin=True)
            db.session.add(row)
        else:
            row.description = description
            row.builtin = True
        roles[name] = row

    for name, description in BUILTIN_RIGHTS.items():
        row = AuthorizationRight.query.filter_by(name=name).one_or_none()
        if row is None:
            row = AuthorizationRight(name=name, description=description, builtin=True)
            db.session.add(row)
        else:
            row.description = description
            row.builtin = True
        rights[name] = row

    db.session.flush()
    return roles, rights


def get_user_account(username):
    username = str(username or "").strip()
    if not username:
        return None
    return UserAccount.query.filter(
        db.func.lower(UserAccount.username) == username.casefold()
    ).one_or_none()


def sync_legacy_directory_user(
    *,
    username,
    display_name=None,
    directory_object_guid=None,
    legacy_role="User",
):
    """Create/update the local account while v1 directory authorization remains live.

    This is a compatibility bridge only.  It never removes locally assigned roles
    or rights.  Runtime authorization continues to use the existing LDAP-derived
    session role until the explicit v2 cut-over.
    """

    username = str(username or "").strip()
    if not username:
        raise ValueError("Journeyman username is required.")

    roles, _rights = ensure_builtin_authorization_definitions()
    account = get_user_account(username)
    is_new = account is None
    if is_new:
        account = UserAccount(username=username)
        db.session.add(account)

    account.username = username
    account.display_name = str(display_name or username).strip() or username
    account.directory_object_guid = str(directory_object_guid or "").strip().lower() or None
    if is_new:
        account.enabled = True
    account.last_login_at = utcnow()

    # The legacy directory decision is migration/bootstrap input only. Once a
    # local account exists, Journeyman-local authorization is authoritative for
    # the new model and later LDAP logins must not recreate removed grants.
    if is_new:
        required_roles = [ROLE_USER]
        if str(legacy_role or "").strip() == "Administrator":
            # Compatibility during the v2 transition: the old directory
            # Administrator role covered all three new administration domains.
            required_roles.extend(
                [ROLE_ADMIN, ROLE_AUTOMATION_ADMIN, ROLE_RESOURCE_ADMIN]
            )

        for role_name in required_roles:
            account.roles.append(roles[role_name])

    db.session.commit()
    return account


def user_has_role(username, role_name):
    account = get_user_account(username)
    return bool(account is not None and account.enabled and account.has_role(role_name))


def user_has_right(username, right_name):
    account = get_user_account(username)
    return bool(account is not None and account.enabled and account.has_right(right_name))


class AuthorizationValidationError(ValueError):
    """Invalid local authorization administration request."""


def _normalise_names(values):
    return {
        str(value or "").strip()
        for value in (values or [])
        if str(value or "").strip()
    }


def normalize_role_names(values):
    """Apply built-in role implications before validating assignments."""

    names = _normalise_names(values)
    if names & ROLES_IMPLYING_USER:
        names.add(ROLE_USER)
    return names


def _resolve_named_authorization(rows, requested_names, *, label):
    requested_names = _normalise_names(requested_names)
    by_name = {row.name: row for row in rows}
    unknown = sorted(requested_names - set(by_name), key=str.casefold)
    if unknown:
        raise AuthorizationValidationError(
            "Unknown {}: {}.".format(label, ", ".join(unknown))
        )
    return [by_name[name] for name in sorted(requested_names, key=str.casefold)]


def create_user_account(
    *,
    username,
    display_name=None,
    enabled=True,
    role_names=None,
    right_names=None,
):
    """Pre-provision a local Journeyman authorization record.

    This does not create an authentication credential. Directory authentication
    (or break-glass administration) remains separate from local authorization.
    """

    username = str(username or "").strip()
    if not username:
        raise AuthorizationValidationError("Username is required.")
    if len(username) > 255:
        raise AuthorizationValidationError("Username must be 255 characters or fewer.")
    if get_user_account(username) is not None:
        raise AuthorizationValidationError("A Journeyman user with that username already exists.")

    roles, rights = ensure_builtin_authorization_definitions()
    account = UserAccount(
        username=username,
        display_name=str(display_name or username).strip() or username,
        enabled=bool(enabled),
    )
    account.roles = _resolve_named_authorization(
        roles.values(), normalize_role_names(role_names), label="role"
    )
    account.rights = _resolve_named_authorization(
        rights.values(), right_names, label="right"
    )
    db.session.add(account)
    db.session.commit()
    return account


def update_user_account_authorization(
    account,
    *,
    display_name=None,
    enabled=True,
    role_names=None,
    right_names=None,
):
    """Replace an account's local role/right assignments."""

    if account is None:
        raise AuthorizationValidationError("Journeyman user does not exist.")

    roles, rights = ensure_builtin_authorization_definitions()
    account.display_name = (
        str(display_name or account.username).strip() or account.username
    )
    account.enabled = bool(enabled)
    account.roles = _resolve_named_authorization(
        roles.values(), normalize_role_names(role_names), label="role"
    )
    account.rights = _resolve_named_authorization(
        rights.values(), right_names, label="right"
    )
    db.session.commit()
    return account
