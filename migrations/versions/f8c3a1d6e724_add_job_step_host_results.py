"""add job step host results

Revision ID: f8c3a1d6e724
Revises: e7b4c2d9a610
"""

from alembic import op
import sqlalchemy as sa


revision = "f8c3a1d6e724"
down_revision = "e7b4c2d9a610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_step_host_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_step_id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_step_id"],
            ["job_step.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_step_id",
            "host",
            name="uq_job_step_host_result_host",
        ),
    )
    op.create_index(
        op.f("ix_job_step_host_result_job_step_id"),
        "job_step_host_result",
        ["job_step_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_job_step_host_result_job_step_id"),
        table_name="job_step_host_result",
    )
    op.drop_table("job_step_host_result")
