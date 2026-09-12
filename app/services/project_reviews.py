"""Technical review workflow for immutable Project revisions."""

from datetime import datetime, timezone

from app import db
from app.models import (
    PROJECT_APPROVAL_DEVELOPMENT,
    PROJECT_APPROVAL_REVIEW_REQUESTED,
    PROJECT_APPROVAL_STALE,
    PROJECT_APPROVAL_TECHNICALLY_APPROVED,
    PROJECT_REVIEW_APPROVED,
    PROJECT_REVIEW_PENDING,
    PROJECT_REVIEW_REJECTED,
    PROJECT_REVIEW_STALE,
    ProjectReview,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_REVIEWER,
    get_user_account,
    user_has_right,
)
from app.services.project_revisions import (
    capture_project_revision,
    project_revision_matches_project,
)


class ProjectReviewError(ValueError):
    """Invalid Project technical-review operation."""


def utcnow():
    return datetime.now(timezone.utc)


def _username(value):
    return str(value or "").strip()


def _same_user(left, right):
    return _username(left).casefold() == _username(right).casefold()


def _pending_review(project):
    if project.id is None:
        return None

    return (
        ProjectReview.query
        .filter_by(
            project_id=project.id,
            status=PROJECT_REVIEW_PENDING,
        )
        .order_by(ProjectReview.id.desc())
        .first()
    )


def eligible_project_reviewers(*, exclude_username=None):
    """Return enabled local users with the Reviewer right."""

    excluded = _username(exclude_username).casefold()
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
            account.has_right(RIGHT_REVIEWER)
            and account.username.casefold() != excluded
        )
    ]


def request_project_review(
    project,
    *,
    requested_by,
    reviewer_username,
):
    """Capture the candidate revision and create a pending technical review."""

    requested_by = _username(requested_by)
    reviewer_username = _username(reviewer_username)

    if project.approval_required is False:
        raise ProjectReviewError(
            "Approval is not required for this Project."
        )

    if not requested_by:
        raise ProjectReviewError("Review requester is required.")
    if not reviewer_username:
        raise ProjectReviewError("Reviewer is required.")
    if _same_user(requested_by, reviewer_username):
        raise ProjectReviewError(
            "A Project requester cannot review their own submission."
        )

    requester = get_user_account(requested_by)
    if requester is None or not requester.enabled:
        raise ProjectReviewError(
            "Review requester must be an enabled Journeyman user."
        )

    reviewer = get_user_account(reviewer_username)
    if reviewer is None or not reviewer.enabled:
        raise ProjectReviewError(
            "Reviewer must be an enabled Journeyman user."
        )
    if not reviewer.has_right(RIGHT_REVIEWER):
        raise ProjectReviewError(
            "Selected reviewer does not have the Reviewer right."
        )

    if project.id is None:
        db.session.flush()

    if _pending_review(project) is not None:
        raise ProjectReviewError(
            "This Project already has a pending technical review."
        )

    revision = capture_project_revision(
        project,
        created_by=requested_by,
    )

    review = ProjectReview(
        project=project,
        revision=revision,
        requested_by=requested_by,
        reviewer_username=reviewer.username,
        status=PROJECT_REVIEW_PENDING,
    )
    project.approval_state = PROJECT_APPROVAL_REVIEW_REQUESTED
    db.session.add(review)
    db.session.flush()
    return review


def project_review_is_current(review):
    """Return whether the Project still matches the submitted revision."""

    return project_revision_matches_project(
        review.revision,
        review.project,
    )


def refresh_project_review_staleness(project):
    """Mark a pending review stale if its submitted definition changed."""

    review = _pending_review(project)
    if review is None:
        return None

    if project_review_is_current(review):
        return review

    review.status = PROJECT_REVIEW_STALE
    review.decided_at = utcnow()
    review.decision_comment = (
        "Project executable definition changed after review was requested."
    )
    project.approval_state = PROJECT_APPROVAL_STALE
    db.session.flush()
    return review


def decide_project_review(
    review,
    *,
    reviewer_username,
    approved,
    comment="",
):
    """Approve or reject a pending technical review."""

    reviewer_username = _username(reviewer_username)
    comment = str(comment or "").strip()

    if review.status != PROJECT_REVIEW_PENDING:
        raise ProjectReviewError(
            "Only a pending technical review can be decided."
        )

    if not reviewer_username:
        raise ProjectReviewError("Reviewer identity is required.")

    if not _same_user(
        reviewer_username,
        review.reviewer_username,
    ):
        raise ProjectReviewError(
            "Only the assigned Reviewer can decide this review."
        )

    if _same_user(reviewer_username, review.requested_by):
        raise ProjectReviewError(
            "A Project requester cannot review their own submission."
        )

    if not user_has_right(reviewer_username, RIGHT_REVIEWER):
        raise ProjectReviewError(
            "Reviewer no longer has the Reviewer right."
        )

    if not project_review_is_current(review):
        review.status = PROJECT_REVIEW_STALE
        review.decided_at = utcnow()
        review.decision_comment = (
            "Project executable definition changed after review was requested."
        )
        review.project.approval_state = PROJECT_APPROVAL_STALE
        db.session.flush()
        return review

    review.decided_at = utcnow()
    review.decision_comment = comment

    if bool(approved):
        review.status = PROJECT_REVIEW_APPROVED
        review.project.approval_state = (
            PROJECT_APPROVAL_TECHNICALLY_APPROVED
        )
    else:
        review.status = PROJECT_REVIEW_REJECTED
        review.project.approval_state = PROJECT_APPROVAL_DEVELOPMENT

    db.session.flush()
    return review
