"""Audit-log administration route."""

import json
import time

from flask import (
    Response,
    abort,
    jsonify,
    render_template,
    request,
    stream_with_context,
)
from sqlalchemy import func, or_

from app import db
from app.auth import current_user_is_admin
from app.models.audit_log import AuditLog
from app.routes import bp
from app.auth import current_username
from app.services.pagination import page_size_for_user


def _clean(value):
    return (value or "").strip()


@bp.get("/audit-log")
def audit_log():
    if not current_user_is_admin():
        abort(403)

    page = request.args.get("page", 1, type=int)
    query_text = _clean(request.args.get("q"))
    action = _clean(request.args.get("action"))
    result = _clean(request.args.get("result"))

    query = AuditLog.query

    if query_text:
        pattern = "%{}%".format(query_text)
        query = query.filter(
            or_(
                AuditLog.actor_username.ilike(pattern),
                AuditLog.action.ilike(pattern),
                AuditLog.object_name.ilike(pattern),
                AuditLog.source_ip.ilike(pattern),
                AuditLog.request_id.ilike(pattern),
            )
        )
    if action:
        query = query.filter(AuditLog.action == action)
    if result:
        query = query.filter(AuditLog.result == result)

    pagination = query.order_by(
        AuditLog.occurred_at.desc(),
        AuditLog.id.desc(),
    ).paginate(page=max(page, 1), per_page=page_size_for_user(current_username()), error_out=False)

    actions = [
        value[0]
        for value in db.session.query(AuditLog.action)
        .distinct()
        .order_by(AuditLog.action.asc())
        .all()
    ]

    return render_template(
        "audit_log.html",
        entries=pagination.items,
        pagination=pagination,
        actions=actions,
        query_text=query_text,
        selected_action=action,
        selected_result=result,
        pagination_args={"q": query_text, "action": action, "result": result},
        latest_audit_id=(
            db.session.query(func.max(AuditLog.id)).scalar() or 0
        ),
    )


@bp.get("/audit-log/latest-id")
def audit_log_latest_id():
    if not current_user_is_admin():
        abort(403)

    latest_id = db.session.query(
        func.max(AuditLog.id)
    ).scalar() or 0

    return jsonify({"latest_id": latest_id})


@bp.get("/audit-log/events")
def audit_log_events():
    if not current_user_is_admin():
        abort(403)

    after_id = max(request.args.get("after_id", 0, type=int), 0)

    @stream_with_context
    def generate():
        last_reported_id = after_id
        heartbeat_counter = 0

        while True:
            db.session.expire_all()
            latest_id = db.session.query(
                func.max(AuditLog.id)
            ).scalar() or 0

            if latest_id > last_reported_id:
                payload = json.dumps(
                    {
                        "latest_id": latest_id,
                        "new_count": latest_id - after_id,
                    }
                )
                yield "event: audit-update\ndata: {}\n\n".format(
                    payload
                )
                last_reported_id = latest_id
                heartbeat_counter = 0
            else:
                heartbeat_counter += 1
                if heartbeat_counter >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

            time.sleep(1)

    response = Response(
        generate(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response
