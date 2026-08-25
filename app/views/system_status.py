"""System status route."""

from flask import abort, render_template

from app.auth import current_user_is_admin
from app.routes import bp
from app.services.system_status import collect_system_status


@bp.get("/system-status")
def system_status():
    if not current_user_is_admin():
        abort(403)

    return render_template(
        "system_status.html",
        status=collect_system_status(),
    )
