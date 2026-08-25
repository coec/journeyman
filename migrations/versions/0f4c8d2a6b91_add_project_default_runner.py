"""add project default runner

Revision ID: 0f4c8d2a6b91
Revises: d7e9b5f2a361
Create Date: 2026-08-07 20:43:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0f4c8d2a6b91"
down_revision = "d7e9b5f2a361"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column("default_runner_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            "ix_project_default_runner_id",
            ["default_runner_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_project_default_runner_id_runner",
            "runner",
            ["default_runner_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Preserve the exact default runner for Projects that were already using
    # the old specific-runner routing mode. Other legacy routing modes cannot
    # be mapped deterministically and remain NULL (built-in local default)
    # until an administrator explicitly edits them.
    op.execute(
        "UPDATE project SET default_runner_id = runner_id "
        "WHERE runner_routing = 'remote_runner' AND runner_id IS NOT NULL"
    )


def downgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(
            "fk_project_default_runner_id_runner",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_project_default_runner_id")
        batch_op.drop_column("default_runner_id")
