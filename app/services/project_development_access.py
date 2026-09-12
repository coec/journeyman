"""Journeyman-local Project development/test access helpers."""

from app.services.authorization import get_user_account


def user_can_develop_project(project, username):
    """Return whether an enabled local account may develop/test ``project``.

    Access is granted directly to the user or inherited through any local
    Journeyman Team assigned to the Project.  This is deliberately separate
    from Package grants: development access does not grant operational
    Package access, and Package access does not grant Project development.
    """

    account = get_user_account(username)
    if account is None or not account.enabled:
        return False

    if any(user.id == account.id for user in project.development_users):
        return True

    account_team_ids = {team.id for team in account.teams}
    return any(
        team.id in account_team_ids
        for team in project.development_teams
    )
