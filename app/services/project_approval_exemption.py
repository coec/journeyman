"""Per-Project approval requirement policy for the 4-eyes workflow."""

from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_APPROVAL_DEVELOPMENT,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_PENDING,
    PROJECT_MANAGEMENT_APPROVAL_STALE,
    PROJECT_REVIEW_PENDING,
    PROJECT_REVIEW_STALE,
    ProjectManagementApproval,
    ProjectReview,
)
from app.services.project_revisions import project_revision_matches_project
from app.services.project_reviews import utcnow


EXEMPTION_STALE_COMMENT = (
    "Project approval requirement was disabled before the decision was completed."
)


class ProjectApprovalRequirementError(ValueError):
    """Invalid per-Project approval requirement change."""


def _latest_management_approval(project, status):
    if project.id is None:
        return None
    return (
        ProjectManagementApproval.query
        .filter_by(project_id=project.id, status=status)
        .order_by(ProjectManagementApproval.id.desc())
        .first()
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


def project_requires_approval(project):
    """Return whether this non-built-in Project participates in 4-eyes approval."""

    return bool(
        project is not None
        and not project.builtin_key
        and project.approval_required is not False
    )


def _close_pending_decisions(project):
    now = utcnow()

    pending_review = _latest_review(project, PROJECT_REVIEW_PENDING)
    if pending_review is not None:
        pending_review.status = PROJECT_REVIEW_STALE
        pending_review.decided_at = now
        pending_review.decision_comment = EXEMPTION_STALE_COMMENT

    pending_approval = _latest_management_approval(
        project,
        PROJECT_MANAGEMENT_APPROVAL_PENDING,
    )
    if pending_approval is not None:
        pending_approval.status = PROJECT_MANAGEMENT_APPROVAL_STALE
        pending_approval.decided_at = now
        pending_approval.decision_comment = EXEMPTION_STALE_COMMENT


def _current_approved_revision(project):
    approval = _latest_management_approval(
        project,
        PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    )
    if approval is None:
        return None
    if not project_revision_matches_project(approval.revision, project):
        return None
    return approval.revision


def set_project_approval_required(project, required):
    """Change whether a Project requires approval and reconcile workflow state.

    Historical decisions are retained. Pending decisions are closed as stale
    when an exemption is granted so they cannot unexpectedly resume later.
    When approval is re-enabled, a still-current prior management approval may
    restore ``approved``; otherwise the Project returns to ``development``.
    """

    if project is None:
        raise ProjectApprovalRequirementError("Project is required.")
    if project.builtin_key:
        raise ProjectApprovalRequirementError(
            "Built-in Projects do not participate in the approval workflow."
        )

    required = bool(required)
    previous = project.approval_required is not False
    if previous == required:
        return previous

    if not required:
        _close_pending_decisions(project)
        project.approval_required = False
        project.approval_state = PROJECT_APPROVAL_DEVELOPMENT
    else:
        project.approval_required = True
        if _current_approved_revision(project) is not None:
            project.approval_state = PROJECT_APPROVAL_APPROVED
        else:
            project.approval_state = PROJECT_APPROVAL_DEVELOPMENT

    db.session.flush()
    return previous
