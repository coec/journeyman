"""Capture immutable Project approval context on queued Jobs."""

from app.models import (
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    JobApprovalProvenance,
    ProjectManagementApproval,
    ProjectReview,
    ProjectRevision,
)
from app.services.approval_workflow import four_eyes_enabled
from app.services.project_revisions import (
    build_project_revision_snapshot,
    project_revision_digest,
    project_revision_matches_project,
)


def _latest_matching_revision(project):
    if project.id is None:
        return None
    for revision in (
        ProjectRevision.query
        .filter_by(project_id=project.id)
        .order_by(ProjectRevision.sequence.desc())
        .all()
    ):
        if project_revision_matches_project(revision, project):
            return revision
    return None


def build_job_approval_provenance(project, *, launch_source="manual"):
    """Return an unsaved immutable approval snapshot for one queued Job."""

    launch_source = str(launch_source or "manual").strip() or "manual"
    enabled = four_eyes_enabled()
    required = project.approval_required is not False

    if not enabled:
        mode = "workflow_disabled"
    elif project.builtin_key:
        mode = "builtin"
    elif not required:
        mode = "exempt"
    else:
        mode = "required"

    execution_context = (
        "operational"
        if launch_source in {"package", "schedule", "reaction", "api_package"}
        else "development_test"
    )

    revision = None
    technical_review = None
    management_approval = None

    if mode == "required" and project.id is not None:
        management_rows = (
            ProjectManagementApproval.query
            .filter_by(project_id=project.id)
            .order_by(ProjectManagementApproval.id.desc())
            .all()
        )
        if project.approval_state == "approved":
            management_approval = next(
                (
                    row
                    for row in management_rows
                    if (
                        row.status == PROJECT_MANAGEMENT_APPROVAL_APPROVED
                        and project_revision_matches_project(row.revision, project)
                    )
                ),
                None,
            )
        elif management_rows:
            management_approval = management_rows[0]

        if management_approval is not None:
            revision = management_approval.revision
            technical_review = management_approval.technical_review
        else:
            technical_review = (
                ProjectReview.query
                .filter_by(project_id=project.id)
                .order_by(ProjectReview.id.desc())
                .first()
            )
            if technical_review is not None:
                revision = technical_review.revision

    if revision is None:
        revision = _latest_matching_revision(project)

    live_digest = project_revision_digest(build_project_revision_snapshot(project))

    return JobApprovalProvenance(
        launch_source=launch_source,
        execution_context=execution_context,
        approval_mode=mode,
        four_eyes_enabled=enabled,
        approval_required=required,
        project_approval_state=project.approval_state,
        revision_id=(revision.id if revision is not None else None),
        revision_sequence=(revision.sequence if revision is not None else None),
        revision_digest=(revision.digest if revision is not None else live_digest),
        technical_review_id=(technical_review.id if technical_review is not None else None),
        technical_reviewer=(technical_review.reviewer_username if technical_review is not None else ""),
        technical_review_status=(technical_review.status if technical_review is not None else ""),
        management_approval_id=(management_approval.id if management_approval is not None else None),
        management_approver=(management_approval.approver_username if management_approval is not None else ""),
        management_approval_status=(management_approval.status if management_approval is not None else ""),
    )


def copy_job_approval_provenance(source_job, target_job):
    source = source_job.approval_provenance
    if source is None:
        return
    target_job.approval_provenance = JobApprovalProvenance(
        launch_source="rerun",
        execution_context=source.execution_context,
        approval_mode=source.approval_mode,
        four_eyes_enabled=source.four_eyes_enabled,
        approval_required=source.approval_required,
        project_approval_state=source.project_approval_state,
        revision_id=source.revision_id,
        revision_sequence=source.revision_sequence,
        revision_digest=source.revision_digest,
        technical_review_id=source.technical_review_id,
        technical_reviewer=source.technical_reviewer,
        technical_review_status=source.technical_review_status,
        management_approval_id=source.management_approval_id,
        management_approver=source.management_approver,
        management_approval_status=source.management_approval_status,
    )
