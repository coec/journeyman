from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_APPROVAL_DEVELOPMENT,
    PROJECT_APPROVAL_REVIEW_REQUESTED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_REVIEW_APPROVED,
    PROJECT_REVIEW_PENDING,
    PROJECT_REVIEW_STALE,
    AuditLog,
    Project,
    ProjectManagementApproval,
    ProjectReview,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_APPROVER,
    ROLE_AUTOMATION_ADMIN,
    ROLE_USER,
    ensure_builtin_authorization_definitions,
)
from app.services.project_approval_exemption import (
    set_project_approval_required,
)
from app.services.project_operational_approval import (
    OPERATIONAL_APPROVAL_REQUIRED_MESSAGE,
    project_operational_approval_error,
)
from app.services.project_revisions import capture_project_revision
from app.services.system_settings import get_or_create_system_settings


def identity_headers(username):
    return {"X-Test-Username": username}


def _enable_four_eyes():
    settings = get_or_create_system_settings()
    settings.four_eyes_enabled = True
    db.session.flush()


def _account(username, *, automation_admin=False, approver=False):
    roles, rights = ensure_builtin_authorization_definitions()
    account = UserAccount(
        username=username,
        display_name=username,
        enabled=True,
    )
    db.session.add(account)
    account.roles.append(roles[ROLE_USER])
    if automation_admin:
        account.roles.append(roles[ROLE_AUTOMATION_ADMIN])
    if approver:
        account.rights.append(rights[RIGHT_APPROVER])
    db.session.flush()
    return account


def _project():
    project = Project(
        name="Approval Exemption Project",
        description="Initial definition",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _approved_history(project):
    revision = capture_project_revision(project, created_by="author")
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
    db.session.add(approval)
    project.approval_state = PROJECT_APPROVAL_APPROVED
    db.session.flush()
    return approval


def test_projects_require_approval_by_default(app):
    with app.app_context():
        project = _project()
        assert project.approval_required is True


def test_exempt_project_bypasses_operational_gate(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()

        assert (
            project_operational_approval_error(project)
            == OPERATIONAL_APPROVAL_REQUIRED_MESSAGE
        )

        set_project_approval_required(project, False)

        assert project.approval_required is False
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT
        assert project_operational_approval_error(project) is None


def test_disabling_requirement_closes_pending_review_as_stale(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        revision = capture_project_revision(project, created_by="author")
        review = ProjectReview(
            project=project,
            revision=revision,
            requested_by="author",
            reviewer_username="reviewer",
            status=PROJECT_REVIEW_PENDING,
        )
        db.session.add(review)
        project.approval_state = PROJECT_APPROVAL_REVIEW_REQUESTED
        db.session.flush()

        set_project_approval_required(project, False)

        assert review.status == PROJECT_REVIEW_STALE
        assert review.decided_at is not None
        assert "approval requirement was disabled" in review.decision_comment
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT


def test_reenabling_requirement_restores_current_approved_revision(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        _approved_history(project)

        set_project_approval_required(project, False)
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT

        set_project_approval_required(project, True)

        assert project.approval_required is True
        assert project.approval_state == PROJECT_APPROVAL_APPROVED


def test_reenabling_requirement_after_change_returns_to_development(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        _approved_history(project)

        set_project_approval_required(project, False)
        project.description = "Changed while exempt"
        db.session.flush()
        set_project_approval_required(project, True)

        assert project.approval_required is True
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT


def test_automation_admin_without_approver_right_cannot_change_requirement(
    app,
    client,
):
    with app.app_context():
        _enable_four_eyes()
        _account("automation", automation_admin=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    response = client.post(
        "/projects/{}/approval-requirement".format(project_id),
        data={},
        headers=identity_headers("automation"),
    )

    assert response.status_code == 403

    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project.approval_required is True


def test_automation_admin_with_approver_right_can_exempt_project(
    app,
    client,
):
    with app.app_context():
        _enable_four_eyes()
        _account("manager", automation_admin=True, approver=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    response = client.post(
        "/projects/{}/approval-requirement".format(project_id),
        data={},
        headers=identity_headers("manager"),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project.approval_required is False
        audit = AuditLog.query.filter_by(
            action="project.approval_requirement.update"
        ).one()
        assert audit.object_id == str(project_id)

    page = client.get(
        "/projects/{}/review".format(project_id),
        headers=identity_headers("manager"),
    )
    assert page.status_code == 200
    assert b"Approval not required" in page.data
    assert b"Require 4-eyes approval for this Project" in page.data


def test_exempt_project_cannot_request_review(app, client):
    with app.app_context():
        _enable_four_eyes()
        _account("author", automation_admin=True)
        _account("manager", automation_admin=True, approver=True)
        project = _project()
        project.approval_required = False
        db.session.commit()
        project_id = project.id

    response = client.post(
        "/projects/{}/review/request".format(project_id),
        data={"reviewer_username": "manager"},
        headers=identity_headers("author"),
    )
    assert response.status_code == 404
