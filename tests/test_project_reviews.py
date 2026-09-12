import pytest

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
    Project,
    UserAccount,
)
from app.services.authorization import (
    RIGHT_REVIEWER,
    ROLE_USER,
    ensure_builtin_authorization_definitions,
)
from app.services.project_reviews import (
    ProjectReviewError,
    decide_project_review,
    eligible_project_reviewers,
    refresh_project_review_staleness,
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
    other_reviewer = UserAccount(
        username="other.reviewer",
        display_name="Other Reviewer",
        enabled=True,
        roles=[roles[ROLE_USER]],
        rights=[rights[RIGHT_REVIEWER]],
    )
    ordinary = UserAccount(
        username="ordinary",
        display_name="Ordinary",
        enabled=True,
        roles=[roles[ROLE_USER]],
    )
    db.session.add_all(
        [author, reviewer, other_reviewer, ordinary]
    )
    db.session.flush()
    return author, reviewer, other_reviewer, ordinary


def _project():
    project = Project(
        name="Technical Review Project",
        description="Initial definition",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def test_eligible_reviewers_require_local_reviewer_right(app):
    with app.app_context():
        author, reviewer, other_reviewer, ordinary = _users()

        rows = eligible_project_reviewers(
            exclude_username=author.username
        )
        usernames = {row.username for row in rows}

        assert reviewer.username in usernames
        assert other_reviewer.username in usernames
        assert ordinary.username not in usernames
        assert author.username not in usernames


def test_request_review_captures_revision_and_changes_state(app):
    with app.app_context():
        author, reviewer, _other, _ordinary = _users()
        project = _project()

        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )
        db.session.commit()

        assert review.status == PROJECT_REVIEW_PENDING
        assert review.revision.project_id == project.id
        assert review.revision.created_by == author.username
        assert review.requested_by == author.username
        assert review.reviewer_username == reviewer.username
        assert project.approval_state == (
            PROJECT_APPROVAL_REVIEW_REQUESTED
        )


def test_requester_cannot_select_self_as_reviewer(app):
    with app.app_context():
        author, _reviewer, _other, _ordinary = _users()
        _roles, rights = ensure_builtin_authorization_definitions()
        author.rights.append(rights[RIGHT_REVIEWER])
        project = _project()

        with pytest.raises(
            ProjectReviewError,
            match="cannot review their own",
        ):
            request_project_review(
                project,
                requested_by=author.username,
                reviewer_username=author.username,
            )


def test_selected_reviewer_must_have_reviewer_right(app):
    with app.app_context():
        author, _reviewer, _other, ordinary = _users()
        project = _project()

        with pytest.raises(
            ProjectReviewError,
            match="does not have the Reviewer right",
        ):
            request_project_review(
                project,
                requested_by=author.username,
                reviewer_username=ordinary.username,
            )


def test_only_assigned_reviewer_can_decide(app):
    with app.app_context():
        author, reviewer, other, _ordinary = _users()
        project = _project()
        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )

        with pytest.raises(
            ProjectReviewError,
            match="assigned Reviewer",
        ):
            decide_project_review(
                review,
                reviewer_username=other.username,
                approved=True,
            )


def test_technical_approval_marks_project_technically_approved(app):
    with app.app_context():
        author, reviewer, _other, _ordinary = _users()
        project = _project()
        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )

        decided = decide_project_review(
            review,
            reviewer_username=reviewer.username,
            approved=True,
            comment="Reviewed exact submitted revision.",
        )
        db.session.commit()

        assert decided.status == PROJECT_REVIEW_APPROVED
        assert decided.decided_at is not None
        assert decided.decision_comment == (
            "Reviewed exact submitted revision."
        )
        assert project.approval_state == (
            PROJECT_APPROVAL_TECHNICALLY_APPROVED
        )


def test_rejection_returns_project_to_development(app):
    with app.app_context():
        author, reviewer, _other, _ordinary = _users()
        project = _project()
        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )

        decide_project_review(
            review,
            reviewer_username=reviewer.username,
            approved=False,
            comment="Please fix the validation step.",
        )

        assert review.status == PROJECT_REVIEW_REJECTED
        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT


def test_changed_project_makes_pending_review_stale(app):
    with app.app_context():
        author, reviewer, _other, _ordinary = _users()
        project = _project()
        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )

        project.description = "Changed after submission"
        db.session.flush()

        refreshed = refresh_project_review_staleness(project)

        assert refreshed.id == review.id
        assert review.status == PROJECT_REVIEW_STALE
        assert project.approval_state == PROJECT_APPROVAL_STALE


def test_review_decision_refuses_changed_revision(app):
    with app.app_context():
        author, reviewer, _other, _ordinary = _users()
        project = _project()
        review = request_project_review(
            project,
            requested_by=author.username,
            reviewer_username=reviewer.username,
        )

        project.description = "Changed before reviewer decision"
        db.session.flush()

        decided = decide_project_review(
            review,
            reviewer_username=reviewer.username,
            approved=True,
        )

        assert decided.status == PROJECT_REVIEW_STALE
        assert project.approval_state == PROJECT_APPROVAL_STALE
        assert decided.decided_at is not None
