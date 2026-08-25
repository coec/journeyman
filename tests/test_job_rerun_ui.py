"""Web UI regression tests for immutable Job reruns."""

from types import SimpleNamespace

from app import db
from app.models import Job, Project
from tests.checks import assert_output_contains, assert_output_equal, assert_output_excludes
from tests.test_csrf import extract_csrf_token


def _job(app, *, status="successful", requested_by="alice"):
    with app.app_context():
        project = Project(name="UI Rerun Project", enabled=True, owner="admin")
        job = Job(
            project=project,
            project_name=project.name,
            status=status,
            requested_by=requested_by,
        )
        db.session.add(job)
        db.session.commit()
        return job.id


def test_terminal_job_pages_offer_rerun(client, app):
    job_id = _job(app, status="successful")

    listing = client.get("/jobs", headers={"X-Test-Username": "alice"})
    detail = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "alice"},
    )

    assert_output_equal(listing.status_code, 200, purpose="Render the Jobs list.")
    assert_output_contains(
        listing.data.decode("utf-8"),
        "/jobs/{}/rerun".format(job_id),
        purpose="Offer rerun from the Jobs list for completed Jobs.",
    )
    assert_output_contains(
        listing.data.decode("utf-8"),
        "action-menu-trigger",
        purpose="Present Job row actions in the shared Actions dropdown.",
    )
    assert_output_contains(
        listing.data.decode("utf-8"),
        ">View</a>",
        purpose="Keep Job details available from every row Actions dropdown.",
    )
    assert_output_contains(
        detail.data.decode("utf-8"),
        "Rerun",
        purpose="Offer rerun from completed Job detail.",
    )
    detail_html = detail.data.decode("utf-8")
    heading_actions = detail_html.split('class="page-heading-actions"', 1)[1].split("</div>", 1)[0]
    assert_output_contains(
        heading_actions,
        "Rerun",
        purpose="Place rerun with the Job detail page heading actions.",
    )
    assert heading_actions.index("Rerun") < heading_actions.index("Save Output")

    preview = client.get(
        "/jobs/{}/rerun".format(job_id),
        headers={"X-Test-Username": "alice"},
    )
    assert_output_equal(preview.status_code, 200, purpose="Render the rerun review page.")
    assert_output_contains(
        preview.data.decode("utf-8"),
        "Review rerun of Job #{}".format(job_id),
        purpose="Require an explicit review step before a web rerun is queued.",
    )
    assert_output_contains(
        preview.data.decode("utf-8"),
        "Confirm rerun",
        purpose="Offer rerun only after showing the saved execution snapshot.",
    )


def test_failed_job_offers_all_and_failed_only_when_failed_hosts_exist(client, app, monkeypatch):
    job_id = _job(app, status="failed")

    import app.views.jobs as jobs_view
    monkeypatch.setattr(
        jobs_view,
        "failed_hosts_for_rerun",
        lambda job: ("host1", "host2") if job.id == job_id else tuple(),
    )

    listing = client.get("/jobs", headers={"X-Test-Username": "alice"})
    detail = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "alice"},
    )
    listing_html = listing.get_data(as_text=True)
    detail_html = detail.get_data(as_text=True)

    assert "Rerun - all" in listing_html
    assert "Rerun - failed only" in listing_html
    assert "scope=failed" in listing_html
    assert "Rerun - all" in detail_html
    assert "Rerun - failed only" in detail_html

    preview = client.get(
        "/jobs/{}/rerun?scope=failed".format(job_id),
        headers={"X-Test-Username": "alice"},
    )
    preview_html = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert "Failed-host-only rerun" in preview_html
    assert 'name="rerun_scope" value="failed"' in preview_html
    assert "Confirm failed-only rerun" in preview_html


def test_failed_job_without_failed_host_results_offers_all_only(client, app):
    job_id = _job(app, status="failed")

    detail = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "alice"},
    )
    html = detail.get_data(as_text=True)

    assert "Rerun - all" in html
    assert "Rerun - failed only" not in html


def test_active_job_does_not_offer_rerun(client, app):
    job_id = _job(app, status="running")
    response = client.get(
        "/jobs/{}".format(job_id),
        headers={"X-Test-Username": "alice"},
    )

    assert_output_equal(response.status_code, 200, purpose="Render active Job detail.")
    assert_output_excludes(
        response.data.decode("utf-8"),
        "/jobs/{}/rerun".format(job_id),
        purpose="Do not offer rerun until the source Job is terminal.",
    )


def test_job_rerun_route_queues_new_job_and_redirects(client, app, monkeypatch):
    source_id = _job(app, status="failed")

    with app.app_context():
        project = Project.query.filter_by(name="UI Rerun Project").one()
        new_job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="alice",
        )
        db.session.add(new_job)
        db.session.commit()
        new_job_id = new_job.id

    calls = []

    def fake_rerun(source_job, *, requested_by, source, scope):
        calls.append((source_job.id, requested_by, source, scope))
        with app.app_context():
            target = db.session.get(Job, new_job_id)
            source_copy = db.session.get(Job, source_id)
            return SimpleNamespace(job=target, source_job=source_copy)

    import app.views.jobs as jobs_view
    monkeypatch.setattr(jobs_view, "rerun_job", fake_rerun)

    page = client.get(
        "/jobs/{}/rerun".format(source_id),
        headers={"X-Test-Username": "alice"},
    )
    token = extract_csrf_token(page)

    unconfirmed = client.post(
        "/jobs/{}/rerun".format(source_id),
        data={"csrf_token": token},
        headers={"X-Test-Username": "alice"},
        follow_redirects=False,
    )
    assert_output_equal(
        unconfirmed.status_code,
        400,
        purpose="Do not queue a web rerun without explicit preview confirmation.",
    )
    assert calls == []

    response = client.post(
        "/jobs/{}/rerun".format(source_id),
        data={"csrf_token": token, "confirm_rerun": "yes"},
        headers={"X-Test-Username": "alice"},
        follow_redirects=False,
    )

    assert_output_equal(response.status_code, 302, purpose="Redirect to the new rerun Job.")
    assert response.headers["Location"].endswith("/jobs/{}".format(new_job_id))
    assert calls == [(source_id, "alice", "Journeyman web interface", "all")]


def test_job_rerun_route_respects_job_visibility(client, app):
    source_id = _job(app, status="successful", requested_by="alice")

    page = client.get(
        "/jobs/{}/rerun".format(source_id),
        headers={"X-Test-Username": "alice"},
    )
    token = extract_csrf_token(page)
    response = client.post(
        "/jobs/{}/rerun".format(source_id),
        data={"csrf_token": token, "confirm_rerun": "yes"},
        headers={"X-Test-Username": "bob"},
    )

    assert_output_equal(response.status_code, 403, purpose="Prevent rerunning another user's Job.")


def test_rerun_preview_displays_preflight_blockers(client, app, monkeypatch):
    job_id = _job(app, status="successful", requested_by="admin")

    import app.views.jobs as jobs_view

    monkeypatch.setattr(
        jobs_view,
        "rerun_preflight_issues",
        lambda job: [
            'Step 1 "The Playbook" requires Environment "Modern ansible" revision '
            '4186a77c69fd on runner "rhel04", but that runner currently has '
            'revision ae6f51b22d65.'
        ],
    )

    response = client.get(
        "/jobs/{}/rerun".format(job_id),
        headers={"X-Test-Username": "admin"},
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Rerun unavailable" in text
    assert "4186a77c69fd" in text
    assert "ae6f51b22d65" in text
    assert 'id="confirm-rerun-button"' not in text
