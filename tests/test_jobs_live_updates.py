"""Jobs list live-update behaviour."""

from app import db
from app.models import Job, Project
from tests.checks import assert_output_contains, assert_output_excludes, assert_output_equal


def _queued_job(app, requested_by="alice"):
    with app.app_context():
        project = Project(
            name="Live Jobs Project",
            enabled=True,
            owner="admin",
        )
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by=requested_by,
        )
        db.session.add(job)
        db.session.commit()
        return job.id


def test_jobs_page_uses_sse_instead_of_two_second_reload(client, app):
    """Active Jobs should subscribe to SSE and contain no timer polling loop."""
    _queued_job(app)

    response = client.get(
        "/jobs",
        headers={"X-Test-Username": "alice"},
    )
    html = response.data.decode("utf-8")

    assert_output_equal(
        response.status_code,
        200,
        purpose="Verify the Jobs list renders for the requesting user.",
    )
    assert_output_contains(
        html,
        "EventSource",
        purpose="Verify active Jobs subscribe to the server-sent event stream.",
    )
    assert_output_contains(
        html,
        "/jobs/events",
        purpose="Verify the Jobs page connects to the Jobs-list SSE endpoint.",
    )
    assert_output_excludes(
        html,
        "setTimeout(function ()",
        purpose="Verify the old two-second full-page polling timer is absent.",
    )


def test_jobs_events_respects_job_visibility(client, app):
    """The SSE route must use the same per-user visibility boundary as /jobs."""
    _queued_job(app, requested_by="alice")

    response = client.get(
        "/jobs/events",
        headers={"X-Test-Username": "bob"},
        buffered=False,
    )

    assert_output_equal(
        response.status_code,
        200,
        purpose="Verify a normal user may establish their own Jobs event stream.",
    )
    assert_output_equal(
        response.content_type,
        "text/event-stream; charset=utf-8",
        purpose="Verify the Jobs live endpoint is delivered as an SSE stream.",
    )
    response.close()
