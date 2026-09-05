"""Authenticated top-bar status endpoints."""

from flask import jsonify

from app.routes import bp, current_user_is_admin, current_username
from app.services.navigation_status import visible_running_job_count


@bp.get("/navigation/status")
def navigation_status():
    """Return lightweight live status for the persistent top navigation."""

    username = current_username()
    return jsonify(
        {
            "running_jobs": visible_running_job_count(
                username,
                is_admin=current_user_is_admin(),
            )
        }
    )
