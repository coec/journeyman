"""add runner draining state

Revision ID: 6f2a9c4d8e10
Revises: 1b0e6a4c9d57
"""
from alembic import op
import sqlalchemy as sa

revision = "6f2a9c4d8e10"
down_revision = "1b0e6a4c9d57"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(sa.Column("drain_job_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("drain_requested_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("drain_reason", sa.String(length=255), nullable=False, server_default=""))
        batch_op.create_index("ix_runner_drain_job_id", ["drain_job_id"], unique=False)


def downgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_index("ix_runner_drain_job_id")
        batch_op.drop_column("drain_reason")
        batch_op.drop_column("drain_requested_at")
        batch_op.drop_column("drain_job_id")
