from datetime import datetime, timezone

from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_REVIEW_APPROVED,
    Project,
    ProjectManagementApproval,
    ProjectReview,
    ProjectSchedule,
)
from app.services.project_operational_approval import (
    OPERATIONAL_APPROVAL_REQUIRED_MESSAGE,
    OPERATIONAL_APPROVAL_STALE_MESSAGE,
    project_is_operationally_approved,
    project_operational_approval_error,
)
from app.services.project_revisions import capture_project_revision
from app.services.schedules import run_claimed_schedule
from app.services.system_settings import get_or_create_system_settings


def _project(name="Operational approval test"):
    settings = get_or_create_system_settings()
    settings.four_eyes_enabled = True
    db.session.flush()

    project = Project(
        name=name,
        description="Initial definition",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _approve(project):
    revision = capture_project_revision(
        project,
        created_by="author",
    )
    review = ProjectReview(
        project=project,
        revision=revision,
        requested_by="author",
        reviewer_username="reviewer",
        status=PROJECT_REVIEW_APPROVED,
    )
    db.session.add(review)
    db.session.flush()

    approval = ProjectManagementApproval(
        project=project,
        revision=revision,
        technical_review=review,
        requested_by="author",
        approver_username="approver",
        status=PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    )
    project.approval_state = PROJECT_APPROVAL_APPROVED
    db.session.add(approval)
    db.session.flush()
    return approval


def test_development_project_is_not_operationally_approved(app):
    with app.app_context():
        project = _project()

        assert not project_is_operationally_approved(project)
        assert (
            project_operational_approval_error(project)
            == OPERATIONAL_APPROVAL_REQUIRED_MESSAGE
        )


def test_approved_current_revision_is_operationally_approved(app):
    with app.app_context():
        project = _project()
        _approve(project)

        assert project_is_operationally_approved(project)
        assert project_operational_approval_error(project) is None


def test_changed_approved_project_fails_closed_as_stale(app):
    with app.app_context():
        project = _project()
        _approve(project)

        project.description = "Changed after approval"
        db.session.flush()

        assert not project_is_operationally_approved(project)
        assert (
            project_operational_approval_error(project)
            == OPERATIONAL_APPROVAL_STALE_MESSAGE
        )


def test_builtin_project_does_not_require_user_approval(app):
    with app.app_context():
        project = _project("Built-in operational project")
        project.builtin_key = "test_builtin"

        assert project_is_operationally_approved(project)


def test_scheduler_disables_unapproved_schedule_before_dispatch(app):
    with app.app_context():
        project = _project()
        now = datetime.now(timezone.utc)
        schedule = ProjectSchedule(
            project=project,
            name="Blocked schedule",
            schedule_type="once",
            timezone_name="UTC",
            start_at=now,
            interval_minutes=None,
            weekdays="",
            enabled=True,
            next_run_at=now,
            claimed_at=now,
            created_by="scheduler.owner",
        )
        db.session.add(schedule)
        db.session.commit()

        result = run_claimed_schedule(schedule.id, now=now)

        db.session.refresh(schedule)
        assert result is None
        assert schedule.enabled is False
        assert schedule.next_run_at is None
        assert schedule.claimed_at is None
        assert schedule.last_error == OPERATIONAL_APPROVAL_REQUIRED_MESSAGE
        assert schedule.last_job_id is None


def _identity_headers(username):
    return {"X-Test-Username": username}


def test_package_launch_route_blocks_unapproved_project(
    app,
    client,
    seeded_packages,
):
    with app.app_context():
        settings = get_or_create_system_settings()
        settings.four_eyes_enabled = True
        project = db.session.get(
            Project,
            seeded_packages["enabled_project"],
        )
        project.approval_state = "development"
        db.session.commit()

    response = client.get(
        "/packages/{}/launch".format(
            seeded_packages["user_package"]
        ),
        headers=_identity_headers("alice"),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        OPERATIONAL_APPROVAL_REQUIRED_MESSAGE.encode()
        in response.data
    )


def test_package_launch_route_blocks_changed_approved_project(
    app,
    client,
    seeded_packages,
):
    with app.app_context():
        settings = get_or_create_system_settings()
        settings.four_eyes_enabled = True
        project = db.session.get(
            Project,
            seeded_packages["enabled_project"],
        )
        project.description = "Changed after fixture approval"
        db.session.commit()

    response = client.get(
        "/packages/{}/launch".format(
            seeded_packages["user_package"]
        ),
        headers=_identity_headers("alice"),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        OPERATIONAL_APPROVAL_STALE_MESSAGE.encode()
        in response.data
    )
