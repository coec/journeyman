from app import db
from app.models import (
    AuthorizationRight,
    AuthorizationRole,
    AuditLog,
    Project,
    ProjectManagementApproval,
    ProjectReview,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_APPROVER,
    RIGHT_REVIEWER,
    ROLE_AUTOMATION_ADMIN,
    ROLE_USER,
)

from app.services.system_settings import get_or_create_system_settings


def identity_headers(username):
    return {"X-Test-Username": username}


def _account(
    username,
    *,
    automation_admin=False,
    reviewer=False,
    approver=False,
):
    account = UserAccount(
        username=username,
        display_name=username,
        enabled=True,
    )
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
            AuthorizationRight.query.filter_by(
                name=RIGHT_REVIEWER
            ).one()
        )
    if approver:
        account.rights.append(
            AuthorizationRight.query.filter_by(
                name=RIGHT_APPROVER
            ).one()
        )
    return account


def _project():
    settings = get_or_create_system_settings()
    settings.four_eyes_enabled = True
    db.session.flush()

    project = Project(
        name="Management Approval Route Project",
        description="Route test",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _technically_approve(client, project_id):
    client.post(
        f"/projects/{project_id}/review/request",
        data={"reviewer_username": "reviewer"},
        headers=identity_headers("author"),
    )

    review_id = ProjectReview.query.filter_by(
        project_id=project_id
    ).one().id

    client.post(
        f"/projects/{project_id}/reviews/{review_id}/decision",
        data={"decision": "approve", "comment": "Technical review complete."},
        headers=identity_headers("reviewer"),
    )
    return review_id


def test_author_can_request_management_approval_and_approver_can_see_project(
    app,
    client,
):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        _account("approver", approver=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    with app.app_context():
        _technically_approve(client, project_id)

    response = client.post(
        f"/projects/{project_id}/management-approval/request",
        data={"approver_username": "approver"},
        headers=identity_headers("author"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    approver_projects = client.get(
        "/projects",
        headers=identity_headers("approver"),
    )
    assert approver_projects.status_code == 200
    assert b"Management Approval Route Project" in approver_projects.data
    assert b"Review &amp; Approval" in approver_projects.data

    approval_page = client.get(
        f"/projects/{project_id}/review",
        headers=identity_headers("approver"),
    )
    assert approval_page.status_code == 200
    assert b"Management decision" in approval_page.data
    assert b"Technical Reviewer" in approval_page.data
    assert b"Exact submitted definition" in approval_page.data

    with app.app_context():
        approval = ProjectManagementApproval.query.filter_by(
            project_id=project_id
        ).one()
        assert approval.approver_username == "approver"
        assert (
            AuditLog.query.filter_by(
                action="project.management_approval.request"
            ).count()
            == 1
        )


def test_assigned_approver_can_approve(app, client):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        _account("approver", approver=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    with app.app_context():
        _technically_approve(client, project_id)

    client.post(
        f"/projects/{project_id}/management-approval/request",
        data={"approver_username": "approver"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        approval_id = ProjectManagementApproval.query.filter_by(
            project_id=project_id
        ).one().id

    response = client.post(
        (
            f"/projects/{project_id}/management-approvals/"
            f"{approval_id}/decision"
        ),
        data={"decision": "approve", "comment": "Approved for operation."},
        headers=identity_headers("approver"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        approval = db.session.get(
            ProjectManagementApproval,
            approval_id,
        )
        project = db.session.get(Project, project_id)
        assert approval.status == "approved"
        assert approval.decision_comment == "Approved for operation."
        assert project.approval_state == "approved"
        assert (
            AuditLog.query.filter_by(
                action="project.management_approval.approve"
            ).count()
            == 1
        )


def test_other_user_cannot_decide_assigned_management_approval(
    app,
    client,
):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        _account("approver", approver=True)
        _account("other", approver=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    with app.app_context():
        _technically_approve(client, project_id)

    client.post(
        f"/projects/{project_id}/management-approval/request",
        data={"approver_username": "approver"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        approval_id = ProjectManagementApproval.query.filter_by(
            project_id=project_id
        ).one().id

    response = client.post(
        (
            f"/projects/{project_id}/management-approvals/"
            f"{approval_id}/decision"
        ),
        data={"decision": "approve"},
        headers=identity_headers("other"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        approval = db.session.get(
            ProjectManagementApproval,
            approval_id,
        )
        project = db.session.get(Project, project_id)
        assert approval.status == "pending"
        assert project.approval_state == "technically_approved"


def test_changed_project_cannot_receive_management_approval_from_ui(
    app,
    client,
):
    with app.app_context():
        _account("author", automation_admin=True)
        _account("reviewer", reviewer=True)
        _account("approver", approver=True)
        project = _project()
        db.session.commit()
        project_id = project.id

    with app.app_context():
        _technically_approve(client, project_id)

    client.post(
        f"/projects/{project_id}/management-approval/request",
        data={"approver_username": "approver"},
        headers=identity_headers("author"),
    )

    with app.app_context():
        approval_id = ProjectManagementApproval.query.filter_by(
            project_id=project_id
        ).one().id
        project = db.session.get(Project, project_id)
        project.description = "Changed after technical approval"
        db.session.commit()

    response = client.post(
        (
            f"/projects/{project_id}/management-approvals/"
            f"{approval_id}/decision"
        ),
        data={"decision": "approve"},
        headers=identity_headers("approver"),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        approval = db.session.get(
            ProjectManagementApproval,
            approval_id,
        )
        project = db.session.get(Project, project_id)
        assert approval.status == "stale"
        assert project.approval_state == "stale"
