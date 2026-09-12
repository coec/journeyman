from app.models import AuthorizationRole, Team, UserAccount
from app.models.project_package import (
    PACKAGE_PRINCIPAL_GROUP,
    PACKAGE_PRINCIPAL_USER,
)
from app.services.authorization import ROLE_USER


def _local_key(kind, object_id):
    return "{}|{}".format(kind, int(object_id))


def _legacy_key(principal_type, principal_name):
    return "legacy|{}|{}".format(
        principal_type,
        str(principal_name).strip().casefold(),
    )


def _guid_key(principal_type, object_guid):
    return "guid|{}|{}".format(
        principal_type,
        str(object_guid).strip().lower(),
    )


def package_principal_context():
    """Return Journeyman-local Package permission choices and validation map.

    v2.0 uses Journeyman's local authorization database for both direct User
    grants and Team grants.  Active Directory is authentication-only and is no
    longer queried while configuring Package permissions.
    """

    user_role = AuthorizationRole.query.filter_by(name=ROLE_USER).one_or_none()
    users = []
    if user_role is not None:
        users = (
            UserAccount.query
            .filter(UserAccount.enabled.is_(True))
            .filter(UserAccount.roles.contains(user_role))
            .order_by(UserAccount.username.asc())
            .all()
        )

    teams = Team.query.order_by(Team.display_name.asc(), Team.id.asc()).all()

    choices = []
    allowed = {}

    for user in users:
        key = _local_key("user", user.id)
        canonical = {
            "principal_type": PACKAGE_PRINCIPAL_USER,
            "principal_name": user.username,
            "principal_object_guid": user.directory_object_guid or None,
            "principal_dn": "",
            "user_account_id": user.id,
            "team_id": None,
        }
        choices.append(
            {
                "key": key,
                "kind": "user",
                "label": user.display_name or user.username,
                "description": user.username,
            }
        )
        allowed[key] = canonical
        allowed[_legacy_key(PACKAGE_PRINCIPAL_USER, user.username)] = canonical
        if user.directory_object_guid:
            allowed[_guid_key(PACKAGE_PRINCIPAL_USER, user.directory_object_guid)] = canonical
            allowed["{}|{}".format(PACKAGE_PRINCIPAL_USER, user.directory_object_guid.lower())] = canonical

    for team in teams:
        key = _local_key("team", team.id)
        canonical = {
            "principal_type": PACKAGE_PRINCIPAL_GROUP,
            "principal_name": team.display_name,
            "principal_object_guid": team.object_guid,
            "principal_dn": team.distinguished_name or "",
            "user_account_id": None,
            "team_id": team.id,
        }
        choices.append(
            {
                "key": key,
                "kind": "team",
                "label": team.display_name,
                "description": "{} member{}".format(
                    len(team.members),
                    "" if len(team.members) == 1 else "s",
                ),
            }
        )
        allowed[key] = canonical
        allowed[_legacy_key(PACKAGE_PRINCIPAL_GROUP, team.display_name)] = canonical
        if team.object_guid:
            allowed[_guid_key(PACKAGE_PRINCIPAL_GROUP, team.object_guid)] = canonical
            allowed["{}|{}".format(PACKAGE_PRINCIPAL_GROUP, team.object_guid.lower())] = canonical

    return {
        "settings": None,
        "choices": choices,
        "allowed": allowed,
        "error": "",
    }
