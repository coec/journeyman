from app import db
from datetime import datetime, timedelta, timezone

from app.models import AuthSession, Job, Project, ProjectPackage


def _storage_fixture():
    return [
        {
            "path": "/var/lib/journeyman",
            "mount_point": "/",
            "status": "healthy",
            "used_percent": 42.0,
            "free_display": "58.0 GiB",
            "error": "",
        },
        {
            "path": "/opt/journeyman",
            "mount_point": "/",
            "status": "warning",
            "used_percent": 82.0,
            "free_display": "18.0 GiB",
            "error": "",
        },
    ]


def test_root_renders_dashboard(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Dashboard" in response.data
    assert b"Recent jobs" in response.data


def test_dashboard_is_default_login_destination(client, app):
    app.config["AUTHENTICATION_DISABLED"] = False

    session_id = "dashboard-test-session"
    with app.app_context():
        now = datetime.now(timezone.utc)
        db.session.add(
            AuthSession(
                session_id=session_id,
                username="admin",
                created_at=now,
                last_seen_at=now,
                directory_checked_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        db.session.commit()

    with client.session_transaction() as session:
        session["journeyman_session_id"] = session_id
        session["journeyman_identity"] = {
            "username": "admin",
            "display_name": "Administrator",
            "role": "Administrator",
            "user_object_guid": None,
            "group_names": [],
            "group_object_guids": [],
            "authenticated_via": "ldap",
        }

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_user_dashboard_only_shows_own_jobs_and_launchable_packages(
    client,
    app,
):
    with app.app_context():
        project = Project(name="Dashboard project", enabled=True)
        db.session.add(project)
        db.session.flush()

        own_job = Job(
            project_id=project.id,
            project_name=project.name,
            status="successful",
            requested_by="user1",
        )
        other_job = Job(
            project_id=project.id,
            project_name="Other user's project",
            status="failed",
            requested_by="user2",
        )
        open_package = ProjectPackage(
            name="Open dashboard package",
            project_id=project.id,
            enabled=True,
            access_mode="authenticated",
        )
        db.session.add_all([own_job, other_job, open_package])
        db.session.commit()

    response = client.get(
        "/",
        headers={"X-Test-Username": "user1"},
    )

    assert response.status_code == 200
    assert b"Dashboard project" in response.data
    assert b"Other user's project" not in response.data
    assert b"Open dashboard package" in response.data


def test_admin_dashboard_shows_storage(client, monkeypatch):
    import app.routes as routes

    monkeypatch.setattr(
        routes,
        "collect_storage_status",
        _storage_fixture,
    )

    response = client.get(
        "/",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert b"/var/lib/journeyman" in response.data
    assert b"/opt/journeyman" in response.data
    assert b"82.0% used" in response.data


def test_user_dashboard_does_not_show_storage(client, monkeypatch):
    import app.routes as routes

    monkeypatch.setattr(
        routes,
        "collect_storage_status",
        _storage_fixture,
    )

    response = client.get(
        "/",
        headers={"X-Test-Username": "ordinary.user"},
    )

    assert response.status_code == 200
    assert b"/var/lib/journeyman" not in response.data
    assert b"/opt/journeyman" not in response.data



def _sidebar_html(response):
    html = response.data.decode("utf-8")
    return html.split('<aside class="sidebar">', 1)[1].split(
        "</aside>",
        1,
    )[0]


def test_user_navigation_only_shows_end_user_items(client):
    response = client.get(
        "/",
        headers={"X-Test-Username": "ordinary.user"},
    )

    assert response.status_code == 200

    sidebar = _sidebar_html(response)

    assert "Dashboard" in sidebar
    assert "Jobs" in sidebar
    assert "Packages" in sidebar

    for admin_item in (
        "Projects",
        "Schedules",
        "Repositories",
        "Credentials",
        "Inventories",
        "Environments",
        "Users",
        "Teams",
        "Settings",
        "Runners",
        "System Status",
        "Audit Log",
    ):
        assert admin_item not in sidebar

    assert sidebar.count('class="nav-heading"') == 1
    assert "WORK" in sidebar


def test_admin_navigation_shows_configuration_sections(client):
    response = client.get(
        "/",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200

    sidebar = _sidebar_html(response)

    for item in (
        "Dashboard",
        "Jobs",
        "Packages",
        "Projects",
        "Schedules",
        "Repositories",
        "Credentials",
        "Inventories",
        "Environments",
        "Users",
        "Teams",
        "Settings",
        "Runners",
        "System Status",
        "Audit Log",
    ):
        assert item in sidebar

    for heading in (
        "WORK",
        "AUTOMATION",
        "RESOURCES",
        "ACCESS CONTROL",
        "SYSTEM",
    ):
        assert heading in sidebar


def test_base_template_displays_version(client):
    from pathlib import Path

    expected_version = (
        Path(__file__).resolve().parents[1]
        / "VERSION"
    ).read_text(encoding="utf-8").strip()

    response = client.get(
        "/",
        headers={"X-Test-Username": "ordinary.user"},
    )

    assert response.status_code == 200
    assert (
        "Journeyman {}".format(expected_version).encode(
            "utf-8"
        )
        in response.data
    )
