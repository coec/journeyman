import pytest

from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_APPROVAL_DEVELOPMENT,
    PROJECT_APPROVAL_STALE,
    PROJECT_APPROVAL_TECHNICALLY_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_PENDING,
    PROJECT_MANAGEMENT_APPROVAL_REJECTED,
    PROJECT_MANAGEMENT_APPROVAL_STALE,
    Project,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_APPROVER,
    RIGHT_REVIEWER,
    ROLE_USER,
    ensure_builtin_authorization_definitions,
)
from app.services.project_management_approvals import (
    ProjectManagementApprovalError,
    decide_project_management_approval,
    eligible_project_approvers,
    request_project_management_approval,
)
from app.services.project_reviews import (
    decide_project_review,
    request_project_review,
)


def _users():
    roles, rights = ensure_builtin_authorization_definitions()

    author = UserAccount(
        username="author",
        display_name="Author",
        enabled=True,
        roles=[roles[ROLE_USER]],
    )
    reviewer = UserAccount(
        username="reviewer",
        display_name="Reviewer",
        enabled=True,
        roles=[roles[ROLE_USER]],
        rights=[rights[RIGHT_REVIEWER]],
    )
    approver = UserAccount(
        username="approver",
        display_name="Approver",
        enabled=True,
        roles=[roles[ROLE_USER]],
        rights=[rights[RIGHT_APPROVER]],
    )
    other_approver = UserAccount(
        username="other.approver",
        display_name="Other Approver",
        enabled=True,
        roles=[roles[ROLE_USER]],
        rights=[rights[RIGHT_APPROVER]],
    )
    ordinary = UserAccount(
        username="ordinary",
        display_name="Ordinary",
        enabled=True,
        roles=[roles[ROLE_USER]],
    )
    db.session.add_all(
        [author, reviewer, approver, other_approver, ordinary]
    )
    db.session.flush()
    return author, reviewer, approver, other_approver, ordinary


def _technically_approved_project(author, reviewer):
    project = Project(
        name="Management Approval Project",
        description="Reviewed definition",
        enabled=True,
        owner=author.username,
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()

    review = request_project_review(
        project,
        requested_by=author.username,
        reviewer_username=reviewer.username,
    )
    decide_project_review(
        review,
        reviewer_username=reviewer.username,
        approved=True,
        comment="Technical review complete.",
    )
    assert project.approval_state == PROJECT_APPROVAL_TECHNICALLY_APPROVED
    return project, review


def test_eligible_approvers_require_local_approver_right(app):
    with app.app_context():
        author, reviewer, approver, other, ordinary = _users()

        rows = eligible_project_approvers(
            exclude_usernames=[author.username, reviewer.username]
        )
        usernames = {row.username for row in rows}

        assert approver.username in usernames
        assert other.username in usernames
        assert author.username not in usernames
        assert reviewer.username not in usernames
        assert ordinary.username not in usernames


def test_management_request_uses_exact_technical_revision(app):
    with app.app_context():
        author, reviewer, approver, _other, _ordinary = _users()
        project, review = _technically_approved_project(author, reviewer)

        approval = request_project_management_approval(
            project,
            requested_by=author.username,
            approver_username=approver.username,
        )

        assert approval.status == PROJECT_MANAGEMENT_APPROVAL_PENDING
        assert approval.technical_review_id == review.id
        assert approval.revision_id == review.revision_id
        assert approval.approver_username == approver.username
        assert project.approval_state == PROJECT_APPROVAL_TECHNICALLY_APPROVED


def test_project_must_be_technically_approved_before_request(app):
    with app.app_context():
        author, _reviewer, approver, _other, _ordinary = _users()
        project = Project(
            name="Development Project",
            description="",
            enabled=True,
            owner=author.username,
            security_scope="private",
        )
        db.session.add(project)

        # SQLAlchemy column defaults are applied on INSERT/flush, so a newly
        # constructed Project can still expose None here. None must retain the
        # model's default semantics: approval is required.
        assert project.approval_required is None

        with pytest.raises(
            ProjectManagementApprovalError,
            match="technically approved",
        ):
            request_project_management_approval(
                project,
                requested_by=author.username,
                approver_username=approver.username,
            )


def test_technical_reviewer_cannot_be_management_approver(app):
    with app.app_context():
        author, reviewer, _approver, _other, _ordinary = _users()
        _roles, rights = ensure_builtin_authorization_definitions()
        reviewer.rights.append(rights[RIGHT_APPROVER])
        project, _review = _technically_approved_project(author, reviewer)

        with pytest.raises(
            ProjectManagementApprovalError,
            match="technical Reviewer cannot also approve",
        ):
            request_project_management_approval(
                project,
                requested_by=author.username,
                approver_username=reviewer.username,
            )


def test_requester_cannot_be_management_approver(app):
    with app.app_context():
        author, reviewer, _approver, _other, _ordinary = _users()
        _roles, rights = ensure_builtin_authorization_definitions()
        author.rights.append(rights[RIGHT_APPROVER])
        project, _review = _technically_approved_project(author, reviewer)

        with pytest.raises(
            ProjectManagementApprovalError,
            match="cannot approve their own",
        ):
            request_project_management_approval(
                project,
                requested_by=author.username,
                approver_username=author.username,
            )


def test_only_assigned_approver_can_decide(app):
    with app.app_context():
        author, reviewer, approver, other, _ordinary = _users()
        project, _review = _technically_approved_project(author, reviewer)
        approval = request_project_management_approval(
            project,
            requested_by=author.username,
            approver_username=approver.username,
        )

        with pytest.raises(
            ProjectManagementApprovalError,
            match="assigned Approver",
        ):
            decide_project_management_approval(
                approval,
                approver_username=other.username,
                approved=True,
            )


def test_management_approval_marks_project_approved(app):
    with app.app_context():
        author, reviewer, approver, _other, _ordinary = _users()
        project, review = _technically_approved_project(author, reviewer)
        approval = request_project_management_approval(
            project,
            requested_by=author.username,
            approver_username=approver.username,
        )

        decided = decide_project_management_approval(
            approval,
            approver_username=approver.username,
            approved=True,
            comment="Approved for operational use.",
        )

        assert decided.status == PROJECT_MANAGEMENT_APPROVAL_APPROVED
        assert decided.technical_review_id == review.id
        assert decided.decided_at is not None
        assert decided.decision_comment == "Approved for operational use."
        assert project.approval_state == PROJECT_APPROVAL_APPROVED


def test_management_rejection_returns_project_to_development(app):
    with app.app_context():
        author, reviewer, approver, _other, _ordinary = _users()
        project, _review = _technically_approved_project(author, reviewer)
        approval = request_project_management_approval(
            project,
            requested_by=author.username,
            approver_username=approver.username,
        )

        decide_project_management_approval(
            approval,
            approver_username=approver.username,
            approved=False,
            comment="Not approved for operational use.",
        )

        assert approval.status == PROJECT_MANAGEMENT_APPROVAL_REJECTED
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT


def test_changed_project_makes_management_decision_stale(app):
    with app.app_context():
        author, reviewer, approver, _other, _ordinary = _users()
        project, _review = _technically_approved_project(author, reviewer)
        approval = request_project_management_approval(
            project,
            requested_by=author.username,
            approver_username=approver.username,
        )

        project.description = "Changed after technical approval"
        db.session.flush()

        decided = decide_project_management_approval(
            approval,
            approver_username=approver.username,
            approved=True,
        )

        assert decided.status == PROJECT_MANAGEMENT_APPROVAL_STALE
        assert project.approval_state == PROJECT_APPROVAL_STALE
        assert decided.decided_at is not None
