from app import db


class JobApprovalProvenance(db.Model):
    """Immutable approval context captured when a Job is queued."""

    __tablename__ = "job_approval_provenance"

    job_id = db.Column(
        db.Integer,
        db.ForeignKey("job.id", ondelete="CASCADE"),
        primary_key=True,
    )
    launch_source = db.Column(db.String(32), nullable=False, default="manual")
    execution_context = db.Column(db.String(32), nullable=False, default="development_test")
    approval_mode = db.Column(db.String(32), nullable=False, default="workflow_disabled")
    four_eyes_enabled = db.Column(db.Boolean, nullable=False, default=False)
    approval_required = db.Column(db.Boolean, nullable=False, default=True)
    project_approval_state = db.Column(db.String(32), nullable=False, default="development")
    revision_id = db.Column(db.Integer, nullable=True)
    revision_sequence = db.Column(db.Integer, nullable=True)
    revision_digest = db.Column(db.String(64), nullable=False, default="")
    technical_review_id = db.Column(db.Integer, nullable=True)
    technical_reviewer = db.Column(db.String(255), nullable=False, default="")
    technical_review_status = db.Column(db.String(32), nullable=False, default="")
    management_approval_id = db.Column(db.Integer, nullable=True)
    management_approver = db.Column(db.String(255), nullable=False, default="")
    management_approval_status = db.Column(db.String(32), nullable=False, default="")

    job = db.relationship("Job", back_populates="approval_provenance")
