from app.models import Team
from app.models.project_package import (
    PACKAGE_PRINCIPAL_GROUP,
    PACKAGE_PRINCIPAL_USER,
)
from app.services.directory import (
    DirectoryError,
    get_directory_client,
)
from app.services.directory_settings import (
    get_or_create_directory_settings,
)


def _guid_key(principal_type, object_guid):
    return "{}|{}".format(
        principal_type,
        str(object_guid).strip().lower(),
    )


def _legacy_key(principal_type, principal_name):
    return "legacy|{}|{}".format(
        principal_type,
        str(principal_name).strip().casefold(),
    )


def package_principal_context():
    """
    Return directory-backed Package permission choices and validation map.

    Direct users are limited to members of the configured Journeyman
    Admin/User role groups. Team choices are limited to AD groups already
    registered through the Teams page.
    """

    settings = get_or_create_directory_settings()
    users = []
    error = ""

    if settings.enabled:
        try:
            users = get_directory_client(
                settings
            ).role_users()
        except DirectoryError as exc:
            error = str(exc)

    teams = Team.query.order_by(
        Team.display_name.asc()
    ).all()

    choices = []
    allowed = {}

    for user in users:
        key = _guid_key(
            PACKAGE_PRINCIPAL_USER,
            user.object_guid,
        )

        canonical = {
            "principal_type": (
                PACKAGE_PRINCIPAL_USER
            ),
            "principal_name": user.username,
            "principal_object_guid": (
                user.object_guid
            ),
            "principal_dn": (
                user.distinguished_name
            ),
        }

        choices.append(
            {
                "key": key,
                "kind": "user",
                "label": "{} ({})".format(
                    user.display_name,
                    user.username,
                ),
                "description": user.role,
            }
        )

        allowed[key] = canonical
        allowed[
            _legacy_key(
                PACKAGE_PRINCIPAL_USER,
                user.username,
            )
        ] = canonical

    for team in teams:
        principal_name = (
            team.sam_account_name
            or team.display_name
        )

        key = _guid_key(
            PACKAGE_PRINCIPAL_GROUP,
            team.object_guid,
        )

        canonical = {
            "principal_type": (
                PACKAGE_PRINCIPAL_GROUP
            ),
            "principal_name": principal_name,
            "principal_object_guid": (
                team.object_guid
            ),
            "principal_dn": (
                team.distinguished_name
            ),
        }

        choices.append(
            {
                "key": key,
                "kind": "team",
                "label": team.display_name,
                "description": (
                    team.sam_account_name
                    or "AD Team"
                ),
            }
        )

        allowed[key] = canonical
        allowed[
            _legacy_key(
                PACKAGE_PRINCIPAL_GROUP,
                principal_name,
            )
        ] = canonical
        allowed[
            _legacy_key(
                PACKAGE_PRINCIPAL_GROUP,
                team.display_name,
            )
        ] = canonical

    return {
        "settings": settings,
        "choices": choices,
        "allowed": allowed,
        "error": error,
    }
