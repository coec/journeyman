"""Immediate stale-state transitions for approval-relevant Project changes."""

from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_APPROVAL_REVIEW_REQUESTED,
    PROJECT_APPROVAL_STALE,
    PROJECT_APPROVAL_TECHNICALLY_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_PENDING,
    PROJECT_MANAGEMENT_APPROVAL_STALE,
    PROJECT_REVIEW_APPROVED,
    PROJECT_REVIEW_PENDING,
    PROJECT_REVIEW_STALE,
    ProjectManagementApproval,
    ProjectReview,
)
from app.services.approval_workflow import four_eyes_enabled
from app.services.project_revisions import project_revision_matches_project
from app.services.project_reviews import utcnow


STALE_COMMENT = (
    "Project executable definition changed after approval workflow submission."
)


def _latest_review(project, status):
    if project.id is None:
        return None
    return (
        ProjectReview.query
        .filter_by(project_id=project.id, status=status)
        .order_by(ProjectReview.id.desc())
        .first()
    )


def _latest_management_approval(project, status):
    if project.id is None:
        return None
    return (
        ProjectManagementApproval.query
        .filter_by(project_id=project.id, status=status)
        .order_by(ProjectManagementApproval.id.desc())
        .first()
    )


def refresh_project_approval_staleness(project):
    """Mark an active approval state stale when executable definition changed.

    Returns True only when this call transitions the Project to ``stale``.
    Historical approved decisions are preserved.  Pending decisions are closed
    as stale because they can no longer be decided against the changed Project.
    """

    if (
        project is None
        or project.builtin_key
        or project.approval_required is False
        or not four_eyes_enabled()
        or project.approval_state == PROJECT_APPROVAL_STALE
    ):
        return False

    comparison_revision = None
    pending_review = None
    pending_approval = None

    if project.approval_state == PROJECT_APPROVAL_REVIEW_REQUESTED:
        pending_review = _latest_review(project, PROJECT_REVIEW_PENDING)
        comparison_revision = (
            pending_review.revision if pending_review is not None else None
        )
    elif project.approval_state == PROJECT_APPROVAL_TECHNICALLY_APPROVED:
        pending_approval = _latest_management_approval(
            project,
            PROJECT_MANAGEMENT_APPROVAL_PENDING,
        )
        if pending_approval is not None:
            comparison_revision = pending_approval.revision
        else:
            approved_review = _latest_review(
                project,
                PROJECT_REVIEW_APPROVED,
            )
            comparison_revision = (
                approved_review.revision
                if approved_review is not None
                else None
            )
    elif project.approval_state == PROJECT_APPROVAL_APPROVED:
        approved = _latest_management_approval(
            project,
            PROJECT_MANAGEMENT_APPROVAL_APPROVED,
        )
        comparison_revision = approved.revision if approved is not None else None
    else:
        return False

    # An active approval state without its supporting immutable revision is
    # inconsistent.  Fail closed rather than silently treating it as current.
    if comparison_revision is not None and project_revision_matches_project(
        comparison_revision,
        project,
    ):
        return False

    now = utcnow()
    if pending_review is not None:
        pending_review.status = PROJECT_REVIEW_STALE
        pending_review.decided_at = now
        pending_review.decision_comment = STALE_COMMENT
    if pending_approval is not None:
        pending_approval.status = PROJECT_MANAGEMENT_APPROVAL_STALE
        pending_approval.decided_at = now
        pending_approval.decision_comment = STALE_COMMENT

    project.approval_state = PROJECT_APPROVAL_STALE
    db.session.flush()
    return True
