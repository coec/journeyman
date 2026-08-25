"""SSE endpoint for the interactive Dispatch progress modal."""

import json
import time

from flask import Response, stream_with_context

from app.routes import bp, current_username
from app.services.dispatch_progress import (
    read_dispatch_progress,
    valid_progress_id,
)


@bp.get("/dispatch-progress/<progress_id>/events")
def dispatch_progress_events(progress_id):
    if not valid_progress_id(progress_id):
        return Response(status=404)

    username = current_username()

    @stream_with_context
    def generate():
        last_sequence = None
        started = time.monotonic()
        last_heartbeat = started

        while time.monotonic() - started < 600:
            payload = read_dispatch_progress(progress_id)

            if payload is not None:
                if payload.get("owner") != username:
                    yield "event: error\ndata: {}\n\n".format(
                        json.dumps({"message": "Operation progress is unavailable."})
                    )
                    return

                sequence = payload.get("sequence")
                if sequence != last_sequence:
                    last_sequence = sequence
                    yield "data: {}\n\n".format(json.dumps(payload))
                    if payload.get("state") in {"done", "error"}:
                        return

            now = time.monotonic()
            if now - last_heartbeat >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            time.sleep(0.2)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response
