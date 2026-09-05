"""Global message/activity indicators shown to authenticated users."""

from datetime import datetime, timedelta, timezone
from html import unescape

from app import db
from app.models import ApiToken, Job, Project


def _job(app, *, requested_by, status):
    with app.app_context():
        project = Project(name=f"Nav {requested_by} {status}", enabled=True, owner="admin")
        job = Job(
            project=project,
            project_name=project.name,
            requested_by=requested_by,
            status=status,
        )
        db.session.add(job)
        db.session.commit()


def test_topbar_indicators_are_available_to_normal_users(client, app):
    _job(app, requested_by="alice", status="running")
    _job(app, requested_by="bob", status="running")

    response = client.get("/", headers={"X-Test-Username": "alice"})
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert 'title="System messages"' in html
    assert 'title="Current activities"' in html
    assert 'data-current-activity-count>1</span>' in html


def test_navigation_status_respects_normal_job_visibility(client, app):
    _job(app, requested_by="alice", status="running")
    _job(app, requested_by="bob", status="running")
    _job(app, requested_by="alice", status="queued")

    response = client.get(
        "/navigation/status",
        headers={"X-Test-Username": "alice"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"running_jobs": 2}


def test_navigation_status_admin_counts_all_running_jobs(client, app):
    _job(app, requested_by="alice", status="running")
    _job(app, requested_by="bob", status="running")

    response = client.get(
        "/navigation/status",
        headers={"X-Test-Username": "admin", "X-Test-Role": "Administrator"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"running_jobs": 2}


def test_expiring_token_appears_in_system_messages_popover(client, app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(
            ApiToken(
                name="expiring",
                username="alice",
                role="User",
                token_digest="d" * 64,
                enabled=True,
                created_at=now - timedelta(days=340),
                expires_at=now + timedelta(days=25),
            )
        )
        db.session.commit()

    response = client.get("/", headers={"X-Test-Username": "alice"})
    html = unescape(response.data.decode("utf-8"))

    assert response.status_code == 200
    assert "System messages" in html
    assert 'API token "expiring" expires on' in html
    assert "security-lifecycle-banner" not in html


def test_running_jobs_filter_is_scoped_to_requesting_user(client, app):
    _job(app, requested_by="alice", status="running")
    _job(app, requested_by="alice", status="queued")
    _job(app, requested_by="bob", status="running")

    response = client.get(
        "/jobs?status=running",
        headers={"X-Test-Username": "alice"},
    )
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "Currently executing work visible to you." in html
    assert html.count('status-running') == 1
    assert "status-queued" not in html


def test_navigation_status_includes_queued_jobs(app, client):
    _job(app, requested_by="alice", status="queued")
    _job(app, requested_by="alice", status="running")

    response = client.get(
        "/navigation/status",
        headers={"X-Test-Username": "alice"},
    )
    assert response.status_code == 200
    assert response.get_json()["running_jobs"] == 2
