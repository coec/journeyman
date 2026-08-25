"""add job step execution slices

Revision ID: 3b7d9f1a5c20
Revises: 2a6c8e4f1b90
"""

from alembic import op
import sqlalchemy as sa


revision = "3b7d9f1a5c20"
down_revision = "2a6c8e4f1b90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_step_execution_slice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_step_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("dispatch_target", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("required_runner_id", sa.Integer(), nullable=True),
        sa.Column("assigned_runner_id", sa.Integer(), nullable=True),
        sa.Column("runner_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("runner_hostname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("required_runner_capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("hosts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("host_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("dispatch_token", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("command", sa.Text(), nullable=False, server_default=""),
        sa.Column("stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("custom_stats_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_step_id"], ["job_step.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["required_runner_id"], ["runner.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_runner_id"], ["runner.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_step_id",
            "position",
            name="uq_job_step_execution_slice_position",
        ),
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_job_step_id"),
        "job_step_execution_slice",
        ["job_step_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_dispatch_target"),
        "job_step_execution_slice",
        ["dispatch_target"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_required_runner_id"),
        "job_step_execution_slice",
        ["required_runner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_assigned_runner_id"),
        "job_step_execution_slice",
        ["assigned_runner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_status"),
        "job_step_execution_slice",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_step_execution_slice_dispatch_token"),
        "job_step_execution_slice",
        ["dispatch_token"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_job_step_execution_slice_dispatch_token"),
        table_name="job_step_execution_slice",
    )
    op.drop_index(
        op.f("ix_job_step_execution_slice_status"),
        table_name="job_step_execution_slice",
    )
    op.drop_index(
        op.f("ix_job_step_execution_slice_assigned_runner_id"),
        table_name="job_step_execution_slice",
    )
    op.drop_index(
        op.f("ix_job_step_execution_slice_required_runner_id"),
        table_name="job_step_execution_slice",
    )
    op.drop_index(
        op.f("ix_job_step_execution_slice_dispatch_target"),
        table_name="job_step_execution_slice",
    )
    op.drop_index(
        op.f("ix_job_step_execution_slice_job_step_id"),
        table_name="job_step_execution_slice",
    )
    op.drop_table("job_step_execution_slice")
