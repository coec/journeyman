from app import db
from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    PROJECT_REVIEW_APPROVED,
    Job,
    Project,
    ProjectManagementApproval,
    ProjectReview,
)
from app.services.job_approval_provenance import (
    build_job_approval_provenance,
    copy_job_approval_provenance,
)
from app.services.project_revisions import capture_project_revision
from app.services.system_settings import get_or_create_system_settings


def _project():
    project = Project(
        name="Provenance Project",
        description="",
        enabled=True,
        owner="author",
        security_scope="private",
    )
    db.session.add(project)
    db.session.flush()
    return project


def test_workflow_disabled_is_snapshotted(app):
    with app.app_context():
        project = _project()
        provenance = build_job_approval_provenance(
            project,
            launch_source="manual",
        )
        assert provenance.four_eyes_enabled is False
        assert provenance.approval_mode == "workflow_disabled"
        assert provenance.execution_context == "development_test"
        assert len(provenance.revision_digest) == 64


def test_approved_operational_job_captures_review_and_approver(app):
    with app.app_context():
        settings = get_or_create_system_settings()
        settings.four_eyes_enabled = True
        project = _project()
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
            approver_username="manager",
            status=PROJECT_MANAGEMENT_APPROVAL_APPROVED,
        )
        project.approval_state = PROJECT_APPROVAL_APPROVED
        db.session.add(approval)
        db.session.flush()

        provenance = build_job_approval_provenance(
            project,
            launch_source="package",
        )
        assert provenance.approval_mode == "required"
        assert provenance.execution_context == "operational"
        assert provenance.revision_id == revision.id
        assert provenance.revision_digest == revision.digest
        assert provenance.technical_reviewer == "reviewer"
        assert provenance.technical_review_status == PROJECT_REVIEW_APPROVED
        assert provenance.management_approver == "manager"
        assert provenance.management_approval_status == PROJECT_MANAGEMENT_APPROVAL_APPROVED


def test_rerun_copies_original_approval_provenance(app):
    with app.app_context():
        project = _project()
        source = Job(
            project_id=project.id,
            project_name=project.name,
            requested_by="author",
        )
        source.approval_provenance = build_job_approval_provenance(
            project,
            launch_source="manual",
        )
        db.session.add(source)
        db.session.flush()
        target = Job(
            project_id=project.id,
            project_name=project.name,
            requested_by="other",
        )
        copy_job_approval_provenance(source, target)
        assert target.approval_provenance.launch_source == "rerun"
        assert target.approval_provenance.revision_digest == source.approval_provenance.revision_digest
        assert target.approval_provenance.approval_mode == source.approval_provenance.approval_mode
