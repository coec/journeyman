from app import db
from app.models import (
    AuthorizationRole,
    AuthorizationRight,
    AuditLog,
    Project,
    ProjectReview,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_REVIEWER,
    ROLE_AUTOMATION_ADMIN,
    ROLE_USER,
)

from app.services.system_settings import get_or_create_system_settings


def identity_headers(username):
    return {"X-Test-Username": username}


def _account(username, *, automation_admin=False, reviewer=False):
    account = UserAccount(
        username=username,
        display_name=username,
        enabled=True,
    )
    # Add first so relationship changes triggered by the role/right lookups
    # are attached to the active SQLAlchemy session before autoflush.
    db.session.add(account)
    account.roles.append(
        AuthorizationRole.query.filter_by(name=ROLE_USER).one()
    )
    if automation_admin:
        account.roles.append(
            AuthorizationRole.query.filter_by(
                name=ROLE_AUTOMATION_ADMIN
            ).one()
        )
    if reviewer:
        account.rights.append(
            AuthorizationRight.query.filter_by(name=RIGHT_REVIEWER).one()
        )
    db.session.add(account)
    return account


def _project():
    settings = get_or_create_system_settings()
    settings.four_eyes_enabled = True
    db.session.flush()

    project = Project(
        name="Review Route Project",
        description="Route test",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def test_author_can_request_review_and_reviewer_can_see_project(app, client):
    with app.app_context():
        author = _account("author", automation_admin=True)
        reviewer = _account("reviewer", reviewer=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    response = client.post(
        f"/projects/{project_id}/review/request",
        data={"reviewer_username": "reviewer"},
        headers=identity_headers("author"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    reviewer_projects = client.get(
        "/projects",
        headers=identity_headers("reviewer"),
    )
    assert reviewer_projects.status_code == 200
    assert b"Review Route Project" in reviewer_projects.data
    assert b"Review &amp; Approval" in reviewer_projects.data

    review_page = client.get(
        f"/projects/{project_id}/review",
        headers=identity_headers("reviewer"),
    )
    assert review_page.status_code == 200
    assert b"Reviewer decision" in review_page.data
    assert b"Exact submitted definition" in review_page.data

    with app.app_context():
        review = ProjectReview.query.filter_by(project_id=project_id).one()
        assert review.reviewer_username == "reviewer"
        assert AuditLog.query.filter_by(action="project.review.request").count() == 1


def test_assigned_reviewer_can_approve(app, client):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    client.post(
        f"/projects/{project_id}/review/request",
        data={"reviewer_username": "reviewer"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        review_id = ProjectReview.query.filter_by(project_id=project_id).one().id

    response = client.post(
        f"/projects/{project_id}/reviews/{review_id}/decision",
        data={"decision": "approve", "comment": "Looks good."},
        headers=identity_headers("reviewer"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        review = db.session.get(ProjectReview, review_id)
        project = db.session.get(Project, project_id)
        assert review.status == "approved"
        assert review.decision_comment == "Looks good."
        assert project.approval_state == "technically_approved"
        assert AuditLog.query.filter_by(action="project.review.approve").count() == 1


def test_other_user_cannot_decide_assigned_review(app, client):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        _account("other", reviewer=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    client.post(
        f"/projects/{project_id}/review/request",
        data={"reviewer_username": "reviewer"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        review_id = ProjectReview.query.filter_by(project_id=project_id).one().id

    response = client.post(
        f"/projects/{project_id}/reviews/{review_id}/decision",
        data={"decision": "approve"},
        headers=identity_headers("other"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        review = db.session.get(ProjectReview, review_id)
        assert review.status == "pending"


def test_changed_project_cannot_be_approved_from_ui(app, client):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    client.post(
        f"/projects/{project_id}/review/request",
        data={"reviewer_username": "reviewer"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        review_id = ProjectReview.query.filter_by(project_id=project_id).one().id
        project = db.session.get(Project, project_id)
        project.description = "Changed after submission"
        db.session.commit()

    response = client.post(
        f"/projects/{project_id}/reviews/{review_id}/decision",
        data={"decision": "approve"},
        headers=identity_headers("reviewer"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        review = db.session.get(ProjectReview, review_id)
        project = db.session.get(Project, project_id)
        assert review.status == "stale"
        assert project.approval_state == "stale"
