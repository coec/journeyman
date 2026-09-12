"""Management approval workflow for technically reviewed Project revisions."""

from datetime import datetime, timezone

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
    PROJECT_REVIEW_APPROVED,
    ProjectManagementApproval,
    ProjectReview,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_APPROVER,
    get_user_account,
    user_has_right,
)
from app.services.project_reviews import project_review_is_current


class ProjectManagementApprovalError(ValueError):
    """Invalid Project management-approval operation."""


def utcnow():
    return datetime.now(timezone.utc)


def _username(value):
    return str(value or "").strip()


def _same_user(left, right):
    return _username(left).casefold() == _username(right).casefold()


def _approved_technical_review(project):
    if project.id is None:
        return None

    return (
        ProjectReview.query
        .filter_by(
            project_id=project.id,
            status=PROJECT_REVIEW_APPROVED,
        )
        .order_by(ProjectReview.id.desc())
        .first()
    )


def _pending_management_approval(project):
    if project.id is None:
        return None

    return (
        ProjectManagementApproval.query
        .filter_by(
            project_id=project.id,
            status=PROJECT_MANAGEMENT_APPROVAL_PENDING,
        )
        .order_by(ProjectManagementApproval.id.desc())
        .first()
    )


def eligible_project_approvers(
    *,
    exclude_usernames=(),
):
    """Return enabled local users with the Approver right."""

    excluded = {
        _username(value).casefold()
        for value in exclude_usernames
        if _username(value)
    }
    rows = (
        UserAccount.query
        .filter(UserAccount.enabled.is_(True))
        .order_by(
            UserAccount.display_name.asc(),
            UserAccount.username.asc(),
        )
        .all()
    )

    return [
        account
        for account in rows
        if (
            account.has_right(RIGHT_APPROVER)
            and account.username.casefold() not in excluded
        )
    ]


def request_project_management_approval(
    project,
    *,
    requested_by,
    approver_username,
):
    """Submit the technically approved revision for management approval."""

    requested_by = _username(requested_by)
    approver_username = _username(approver_username)

    if project.approval_required is False:
        raise ProjectManagementApprovalError(
            "Approval is not required for this Project."
        )

    if project.approval_state != PROJECT_APPROVAL_TECHNICALLY_APPROVED:
        raise ProjectManagementApprovalError(
            "Project must be technically approved before management approval."
        )
    if not requested_by:
        raise ProjectManagementApprovalError(
            "Management approval requester is required."
        )
    if not approver_username:
        raise ProjectManagementApprovalError("Approver is required.")
    if _same_user(requested_by, approver_username):
        raise ProjectManagementApprovalError(
            "A Project requester cannot approve their own submission."
        )

    requester = get_user_account(requested_by)
    if requester is None or not requester.enabled:
        raise ProjectManagementApprovalError(
            "Management approval requester must be an enabled Journeyman user."
        )

    approver = get_user_account(approver_username)
    if approver is None or not approver.enabled:
        raise ProjectManagementApprovalError(
            "Approver must be an enabled Journeyman user."
        )
    if not approver.has_right(RIGHT_APPROVER):
        raise ProjectManagementApprovalError(
            "Selected approver does not have the Approver right."
        )

    review = _approved_technical_review(project)
    if review is None:
        raise ProjectManagementApprovalError(
            "Project has no approved technical review."
        )
    if not project_review_is_current(review):
        project.approval_state = PROJECT_APPROVAL_STALE
        db.session.flush()
        raise ProjectManagementApprovalError(
            "Project changed after technical review and must be reviewed again."
        )
    if _same_user(approver.username, review.reviewer_username):
        raise ProjectManagementApprovalError(
            "The technical Reviewer cannot also approve the same revision."
        )
    if _pending_management_approval(project) is not None:
        raise ProjectManagementApprovalError(
            "This Project already has a pending management approval."
        )

    approval = ProjectManagementApproval(
        project=project,
        revision=review.revision,
        technical_review=review,
        requested_by=requested_by,
        approver_username=approver.username,
        status=PROJECT_MANAGEMENT_APPROVAL_PENDING,
    )
    db.session.add(approval)
    db.session.flush()
    return approval


def project_management_approval_is_current(approval):
    """Return whether the Project still matches the reviewed revision."""

    return project_review_is_current(approval.technical_review)


def decide_project_management_approval(
    approval,
    *,
    approver_username,
    approved,
    comment="",
):
    """Approve or reject a pending management approval."""

    approver_username = _username(approver_username)
    comment = str(comment or "").strip()

    if approval.status != PROJECT_MANAGEMENT_APPROVAL_PENDING:
        raise ProjectManagementApprovalError(
            "Only a pending management approval can be decided."
        )
    if not approver_username:
        raise ProjectManagementApprovalError("Approver identity is required.")
    if not _same_user(
        approver_username,
        approval.approver_username,
    ):
        raise ProjectManagementApprovalError(
            "Only the assigned Approver can decide this approval."
        )
    if _same_user(approver_username, approval.requested_by):
        raise ProjectManagementApprovalError(
            "A Project requester cannot approve their own submission."
        )
    if _same_user(
        approver_username,
        approval.technical_review.reviewer_username,
    ):
        raise ProjectManagementApprovalError(
            "The technical Reviewer cannot also approve the same revision."
        )
    if not user_has_right(approver_username, RIGHT_APPROVER):
        raise ProjectManagementApprovalError(
            "Approver no longer has the Approver right."
        )

    if not project_management_approval_is_current(approval):
        approval.status = PROJECT_MANAGEMENT_APPROVAL_STALE
        approval.decided_at = utcnow()
        approval.decision_comment = (
            "Project executable definition changed after technical approval."
        )
        approval.project.approval_state = PROJECT_APPROVAL_STALE
        db.session.flush()
        return approval

    approval.decided_at = utcnow()
    approval.decision_comment = comment

    if bool(approved):
        approval.status = PROJECT_MANAGEMENT_APPROVAL_APPROVED
        approval.project.approval_state = PROJECT_APPROVAL_APPROVED
    else:
        approval.status = PROJECT_MANAGEMENT_APPROVAL_REJECTED
        approval.project.approval_state = PROJECT_APPROVAL_DEVELOPMENT

    db.session.flush()
    return approval
