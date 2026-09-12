"""add immutable Job approval provenance

Revision ID: f7b9d1e3a618
Revises: e6a8c0d2f507
"""
from alembic import op
import sqlalchemy as sa

revision = "f7b9d1e3a618"
down_revision = "e6a8c0d2f507"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_approval_provenance",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("launch_source", sa.String(length=32), nullable=False),
        sa.Column("execution_context", sa.String(length=32), nullable=False),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
        sa.Column("four_eyes_enabled", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("project_approval_state", sa.String(length=32), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=True),
        sa.Column("revision_digest", sa.String(length=64), nullable=False),
        sa.Column("technical_review_id", sa.Integer(), nullable=True),
        sa.Column("technical_reviewer", sa.String(length=255), nullable=False),
        sa.Column("technical_review_status", sa.String(length=32), nullable=False),
        sa.Column("management_approval_id", sa.Integer(), nullable=True),
        sa.Column("management_approver", sa.String(length=255), nullable=False),
        sa.Column("management_approval_status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade():
    op.drop_table("job_approval_provenance")
