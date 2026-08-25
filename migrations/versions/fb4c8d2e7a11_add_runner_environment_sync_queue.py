"""add runner environment synchronization queue

Revision ID: fb4c8d2e7a11
Revises: fa3b7c9d2e10
"""
from alembic import op
import sqlalchemy as sa

revision = "fb4c8d2e7a11"
down_revision = "fa3b7c9d2e10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runner_environment_sync",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column(
            "requested_revision",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name="fk_runner_environment_sync_environment_id_environment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runner.id"],
            name="fk_runner_environment_sync_runner_id_runner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runner_id",
            "environment_id",
            name="uq_runner_environment_sync_runner_environment",
        ),
    )
    op.create_index(
        "ix_runner_environment_sync_runner_id",
        "runner_environment_sync",
        ["runner_id"],
        unique=False,
    )
    op.create_index(
        "ix_runner_environment_sync_environment_id",
        "runner_environment_sync",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_runner_environment_sync_status",
        "runner_environment_sync",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_runner_environment_sync_status",
        table_name="runner_environment_sync",
    )
    op.drop_index(
        "ix_runner_environment_sync_environment_id",
        table_name="runner_environment_sync",
    )
    op.drop_index(
        "ix_runner_environment_sync_runner_id",
        table_name="runner_environment_sync",
    )
    op.drop_table("runner_environment_sync")
