"""Project technical-review web routes."""

import json

from app.models import (
    PROJECT_APPROVAL_TECHNICALLY_APPROVED,
    PROJECT_MANAGEMENT_APPROVAL_PENDING,
    PROJECT_REVIEW_APPROVED,
    PROJECT_REVIEW_PENDING,
    Project,
    ProjectManagementApproval,
    ProjectReview,
)
from app.routes import (
    abort,
    bp,
    current_user_can_develop_project,
    current_user_can_manage_automation,
    current_user_can_view_project,
    current_username,
    db,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from app.auth import current_user_has_right
from app.services.audit import record_audit_event
from app.services.approval_workflow import four_eyes_enabled
from app.services.authorization import RIGHT_APPROVER
from app.services.project_approval_exemption import (
    ProjectApprovalRequirementError,
    set_project_approval_required,
)
from app.services.project_management_approvals import (
    ProjectManagementApprovalError,
    decide_project_management_approval,
    eligible_project_approvers,
    project_management_approval_is_current,
    request_project_management_approval,
)
from app.services.project_reviews import (
    ProjectReviewError,
    decide_project_review,
    eligible_project_reviewers,
    project_review_is_current,
    refresh_project_review_staleness,
    request_project_review,
)


def _require_approval_workflow():
    if not four_eyes_enabled():
        abort(404)


def _can_manage_approval_requirement():
    return bool(
        current_user_can_manage_automation()
        and current_user_has_right(RIGHT_APPROVER)
    )


def _assigned_review(project, username):
    username = str(username or "").strip().casefold()
    if not username:
        return None

    reviews = sorted(project.reviews, key=lambda item: item.id or 0, reverse=True)
    for review in reviews:
        if str(review.reviewer_username or "").strip().casefold() == username:
            return review
    return None


def _assigned_management_approval(project, username):
    username = str(username or "").strip().casefold()
    if not username:
        return None

    approvals = sorted(
        project.management_approvals,
        key=lambda item: item.id or 0,
        reverse=True,
    )
    for approval in approvals:
        if (
            str(approval.approver_username or "").strip().casefold()
            == username
        ):
            return approval
    return None


def _can_view_review_page(project):
    username = current_username()
    return bool(
        current_user_can_view_project(project)
        or _assigned_review(project, username) is not None
        or _assigned_management_approval(project, username) is not None
    )


def _review_context(project):
    reviews = sorted(
        project.reviews,
        key=lambda item: item.id or 0,
        reverse=True,
    )
    approvals = sorted(
        project.management_approvals,
        key=lambda item: item.id or 0,
        reverse=True,
    )
    pending_review = next(
        (
            review
            for review in reviews
            if review.status == PROJECT_REVIEW_PENDING
        ),
        None,
    )
    pending_management_approval = next(
        (
            approval
            for approval in approvals
            if approval.status == PROJECT_MANAGEMENT_APPROVAL_PENDING
        ),
        None,
    )
    latest_approved_review = next(
        (
            review
            for review in reviews
            if review.status == PROJECT_REVIEW_APPROVED
        ),
        None,
    )

    username = current_username()
    assigned_review = _assigned_review(project, username)
    assigned_management_approval = _assigned_management_approval(
        project,
        username,
    )
    can_develop = current_user_can_develop_project(project)

    display_review = (
        pending_review
        or (
            pending_management_approval.technical_review
            if pending_management_approval is not None
            else None
        )
        or latest_approved_review
    )
    submitted_snapshot = (
        display_review.revision.snapshot()
        if display_review is not None
        else None
    )
    submitted_snapshot_json = (
        json.dumps(submitted_snapshot, indent=2, sort_keys=True)
        if submitted_snapshot is not None
        else ""
    )

    can_request_management_approval = bool(
        project.approval_required
        and can_develop
        and project.approval_state
        == PROJECT_APPROVAL_TECHNICALLY_APPROVED
        and latest_approved_review is not None
        and pending_management_approval is None
    )
    approvers = []
    if can_request_management_approval:
        approvers = eligible_project_approvers(
            exclude_usernames=(
                username,
                latest_approved_review.reviewer_username,
            )
        )

    return {
        "project": project,
        "reviews": reviews,
        "management_approvals": approvals,
        "pending_review": pending_review,
        "pending_management_approval": pending_management_approval,
        "latest_approved_review": latest_approved_review,
        "display_review": display_review,
        "assigned_review": assigned_review,
        "assigned_management_approval": assigned_management_approval,
        "can_develop_project": can_develop,
        "can_manage_approval_requirement": _can_manage_approval_requirement(),
        "approval_required": bool(project.approval_required),
        "can_request_management_approval": can_request_management_approval,
        "reviewers": (
            eligible_project_reviewers(exclude_username=username)
            if project.approval_required and can_develop and pending_review is None
            else []
        ),
        "approvers": approvers,
        "pending_review_is_current": (
            project_review_is_current(pending_review)
            if pending_review is not None
            else True
        ),
        "pending_management_approval_is_current": (
            project_management_approval_is_current(
                pending_management_approval
            )
            if pending_management_approval is not None
            else True
        ),
        "submitted_snapshot": submitted_snapshot,
        "submitted_snapshot_json": submitted_snapshot_json,
    }


@bp.get("/projects/<int:project_id>/review")
def project_review(project_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not _can_view_review_page(project):
        abort(403)

    return render_template(
        "project_review.html",
        **_review_context(project),
    )


@bp.post("/projects/<int:project_id>/approval-requirement")
def project_approval_requirement(project_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not _can_manage_approval_requirement():
        abort(403)

    required = request.form.get("approval_required") == "on"
    previous = bool(project.approval_required)
    try:
        set_project_approval_required(project, required)
        db.session.commit()
    except ProjectApprovalRequirementError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.project_review", project_id=project.id))

    record_audit_event(
        "project.approval_requirement.update",
        object_type="project",
        object_id=project.id,
        object_name=project.name,
        details={
            "previous_approval_required": previous,
            "approval_required": required,
        },
    )
    flash(
        (
            "Approval is now required for this Project."
            if required
            else "Approval is no longer required for this Project."
        ),
        "success",
    )
    return redirect(url_for("main.project_review", project_id=project.id))


@bp.post("/projects/<int:project_id>/review/request")
def project_review_request(project_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not project.approval_required:
        abort(404)
    if not current_user_can_develop_project(project):
        abort(403)

    username = current_username()
    reviewer_username = str(request.form.get("reviewer_username") or "").strip()

    try:
        refresh_project_review_staleness(project)
        review = request_project_review(
            project,
            requested_by=username,
            reviewer_username=reviewer_username,
        )
        db.session.commit()
    except ProjectReviewError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.project_review", project_id=project.id))

    record_audit_event(
        "project.review.request",
        object_type="project",
        object_id=project.id,
        object_name=project.name,
        details={
            "review_id": review.id,
            "revision_id": review.revision_id,
            "revision_sequence": review.revision.sequence,
            "revision_digest": review.revision.digest,
            "reviewer_username": review.reviewer_username,
        },
    )
    flash(
        'Technical review requested from "{}".'.format(review.reviewer_username),
        "success",
    )
    return redirect(url_for("main.project_review", project_id=project.id))


@bp.post("/projects/<int:project_id>/reviews/<int:review_id>/decision")
def project_review_decision(project_id, review_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not project.approval_required:
        abort(404)

    review = db.get_or_404(ProjectReview, review_id)
    if review.project_id != project.id:
        abort(404)

    decision = str(request.form.get("decision") or "").strip().lower()
    if decision not in {"approve", "reject"}:
        flash("Choose Approve or Reject.", "error")
        return redirect(url_for("main.project_review", project_id=project.id))

    username = current_username()
    try:
        decided = decide_project_review(
            review,
            reviewer_username=username,
            approved=(decision == "approve"),
            comment=request.form.get("comment") or "",
        )
        db.session.commit()
    except ProjectReviewError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.project_review", project_id=project.id))

    record_audit_event(
        "project.review.{}".format(decision),
        object_type="project",
        object_id=project.id,
        object_name=project.name,
        details={
            "review_id": decided.id,
            "revision_id": decided.revision_id,
            "revision_sequence": decided.revision.sequence,
            "revision_digest": decided.revision.digest,
            "review_status": decided.status,
        },
    )

    if decided.status == "stale":
        flash(
            "The Project changed after review was requested. The submitted review is now stale.",
            "warning",
        )
    elif decision == "approve":
        flash("Technical review approved.", "success")
    else:
        flash("Technical review rejected.", "success")

    return redirect(url_for("main.project_review", project_id=project.id))

@bp.post("/projects/<int:project_id>/management-approval/request")
def project_management_approval_request(project_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not project.approval_required:
        abort(404)
    if not current_user_can_develop_project(project):
        abort(403)

    username = current_username()
    approver_username = str(
        request.form.get("approver_username") or ""
    ).strip()

    try:
        approval = request_project_management_approval(
            project,
            requested_by=username,
            approver_username=approver_username,
        )
        db.session.commit()
    except ProjectManagementApprovalError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for("main.project_review", project_id=project.id)
        )

    record_audit_event(
        "project.management_approval.request",
        object_type="project",
        object_id=project.id,
        object_name=project.name,
        details={
            "management_approval_id": approval.id,
            "technical_review_id": approval.technical_review_id,
            "revision_id": approval.revision_id,
            "revision_sequence": approval.revision.sequence,
            "revision_digest": approval.revision.digest,
            "approver_username": approval.approver_username,
        },
    )
    flash(
        'Management approval requested from "{}".'.format(
            approval.approver_username
        ),
        "success",
    )
    return redirect(
        url_for("main.project_review", project_id=project.id)
    )


@bp.post(
    "/projects/<int:project_id>/management-approvals/"
    "<int:approval_id>/decision"
)
def project_management_approval_decision(project_id, approval_id):
    _require_approval_workflow()
    project = db.get_or_404(Project, project_id)
    if project.builtin_key is not None:
        abort(404)
    if not project.approval_required:
        abort(404)

    approval = db.get_or_404(
        ProjectManagementApproval,
        approval_id,
    )
    if approval.project_id != project.id:
        abort(404)

    decision = str(
        request.form.get("decision") or ""
    ).strip().lower()
    if decision not in {"approve", "reject"}:
        flash("Choose Approve or Reject.", "error")
        return redirect(
            url_for("main.project_review", project_id=project.id)
        )

    username = current_username()
    try:
        decided = decide_project_management_approval(
            approval,
            approver_username=username,
            approved=(decision == "approve"),
            comment=request.form.get("comment") or "",
        )
        db.session.commit()
    except ProjectManagementApprovalError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for("main.project_review", project_id=project.id)
        )

    record_audit_event(
        "project.management_approval.{}".format(decision),
        object_type="project",
        object_id=project.id,
        object_name=project.name,
        details={
            "management_approval_id": decided.id,
            "technical_review_id": decided.technical_review_id,
            "revision_id": decided.revision_id,
            "revision_sequence": decided.revision.sequence,
            "revision_digest": decided.revision.digest,
            "management_approval_status": decided.status,
        },
    )

    if decided.status == "stale":
        flash(
            "The Project changed after technical approval. "
            "The management approval is now stale.",
            "warning",
        )
    elif decision == "approve":
        flash("Management approval granted.", "success")
    else:
        flash("Management approval rejected.", "success")

    return redirect(
        url_for("main.project_review", project_id=project.id)
    )
