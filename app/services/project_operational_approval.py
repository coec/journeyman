"""Operational execution gate for approved Project revisions."""

from app.models import (
    PROJECT_APPROVAL_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_APPROVED,
    ProjectManagementApproval,
)
from app.services.approval_workflow import four_eyes_enabled
from app.services.project_revisions import project_revision_matches_project


OPERATIONAL_APPROVAL_REQUIRED_MESSAGE = (
    "Project is not approved for operational execution."
)
OPERATIONAL_APPROVAL_STALE_MESSAGE = (
    "Project approval is stale because its executable definition changed."
)
OPERATIONAL_APPROVAL_RECORD_MISSING_MESSAGE = (
    "Project has no recorded management approval for operational execution."
)


def _latest_approved_management_approval(project):
    if project.id is None:
        return None

    return (
        ProjectManagementApproval.query
        .filter_by(
            project_id=project.id,
            status=PROJECT_MANAGEMENT_APPROVAL_APPROVED,
        )
        .order_by(ProjectManagementApproval.id.desc())
        .first()
    )


def project_operational_approval_error(project):
    """Return a blocking reason, or None when operational execution is allowed."""

    if project is None:
        return OPERATIONAL_APPROVAL_REQUIRED_MESSAGE

    # When the system-wide workflow is suspended, approval state and retained
    # approval history do not restrict normal Journeyman operation.
    if not four_eyes_enabled():
        return None

    # Journeyman-owned built-ins are trusted system automation and do not
    # participate in the user Project approval workflow.
    if project.builtin_key:
        return None

    if project.approval_required is False:
        return None

    if project.approval_state != PROJECT_APPROVAL_APPROVED:
        return OPERATIONAL_APPROVAL_REQUIRED_MESSAGE

    approval = _latest_approved_management_approval(project)
    if approval is None:
        return OPERATIONAL_APPROVAL_RECORD_MISSING_MESSAGE

    if not project_revision_matches_project(
        approval.revision,
        project,
    ):
        return OPERATIONAL_APPROVAL_STALE_MESSAGE

    return None


def project_is_operationally_approved(project):
    return project_operational_approval_error(project) is None
