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
    Project,
    ProjectManagementApproval,
    ProjectReview,
    ProjectStep,
)
from app.services.project_approval_staleness import (
    refresh_project_approval_staleness,
)
from app.services.project_revisions import capture_project_revision
from app.services.system_settings import get_or_create_system_settings


def _enable_four_eyes():
    settings = get_or_create_system_settings()
    settings.four_eyes_enabled = True
    db.session.flush()


def _project():
    project = Project(
        name="Staleness Project",
        description="Approved definition",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _revision(project):
    return capture_project_revision(project, created_by="author")


def test_pending_technical_review_becomes_stale_after_definition_change(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        revision = _revision(project)
        review = ProjectReview(
            project=project,
            revision=revision,
            requested_by="author",
            reviewer_username="reviewer",
            status=PROJECT_REVIEW_PENDING,
        )
        project.approval_state = PROJECT_APPROVAL_REVIEW_REQUESTED
        db.session.add(review)
        db.session.flush()

        project.description = "Changed after submission"

        assert refresh_project_approval_staleness(project) is True
        assert project.approval_state == PROJECT_APPROVAL_STALE
        assert review.status == PROJECT_REVIEW_STALE
        assert review.decided_at is not None


def test_pending_management_approval_becomes_stale_after_change(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        revision = _revision(project)
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
            status=PROJECT_MANAGEMENT_APPROVAL_PENDING,
        )
        project.approval_state = PROJECT_APPROVAL_TECHNICALLY_APPROVED
        db.session.add(approval)
        db.session.flush()

        project.max_parallel_steps = project.max_parallel_steps + 1

        assert refresh_project_approval_staleness(project) is True
        assert project.approval_state == PROJECT_APPROVAL_STALE
        assert approval.status == PROJECT_MANAGEMENT_APPROVAL_STALE
        assert approval.decided_at is not None


def test_approved_project_becomes_stale_but_keeps_approval_history(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        revision = _revision(project)
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

        project.description = "Changed after approval"

        assert refresh_project_approval_staleness(project) is True
        assert project.approval_state == PROJECT_APPROVAL_STALE
        assert approval.status == PROJECT_MANAGEMENT_APPROVAL_APPROVED
        assert review.status == PROJECT_REVIEW_APPROVED


def test_non_revision_project_metadata_does_not_make_approval_stale(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        revision = _revision(project)
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

        # owner/security_scope govern local administration/access and are not
        # part of the executable revision snapshot.
        project.owner = "different.owner"
        project.security_scope = "shared"

        assert refresh_project_approval_staleness(project) is False
        assert project.approval_state == PROJECT_APPROVAL_APPROVED


def test_four_eyes_off_does_not_mutate_approval_state(app):
    with app.app_context():
        settings = get_or_create_system_settings()
        settings.four_eyes_enabled = False
        project = _project()
        revision = _revision(project)
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

        project.description = "Changed while workflow disabled"

        assert refresh_project_approval_staleness(project) is False
        assert project.approval_state == PROJECT_APPROVAL_APPROVED


def test_replaced_step_row_ids_do_not_invalidate_same_definition(app):
    with app.app_context():
        _enable_four_eyes()
        project = _project()
        first_step = ProjectStep(
            project=project,
            position=1,
            name="Run playbook",
            playbook="site.yml",
            execution_type="ansible",
        )
        db.session.add(first_step)
        db.session.flush()
        revision = _revision(project)
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

        db.session.delete(first_step)
        db.session.flush()

        # SQLite may reuse the deleted highest INTEGER PRIMARY KEY value.
        # Consume that value on an unrelated Project so this test genuinely
        # exercises semantic equality across different ProjectStep row IDs.
        burner_project = Project(
            name="Staleness row-id burner",
            description="Test-only unrelated Project",
            enabled=True,
            owner="author",
            security_scope="private",
        )
        burner_step = ProjectStep(
            project=burner_project,
            position=1,
            name="Burn row id",
            playbook="burner.yml",
            execution_type="ansible",
        )
        db.session.add_all([burner_project, burner_step])
        db.session.flush()

        replacement = ProjectStep(
            project=project,
            position=1,
            name="Run playbook",
            playbook="site.yml",
            execution_type="ansible",
        )
        db.session.add(replacement)
        db.session.flush()

        assert replacement.id != first_step.id
        assert refresh_project_approval_staleness(project) is False
        assert project.approval_state == PROJECT_APPROVAL_APPROVED
