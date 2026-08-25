"""add job default runner snapshot

Revision ID: 5d9f3b7a2c40
Revises: 4c8e2a7d6b31
"""

from alembic import op
import sqlalchemy as sa


revision = "5d9f3b7a2c40"
down_revision = "4c8e2a7d6b31"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column("default_runner_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_job_default_runner_id",
            "runner",
            ["default_runner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_job_default_runner_id",
            ["default_runner_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_index("ix_job_default_runner_id")
        batch_op.drop_constraint(
            "fk_job_default_runner_id",
            type_="foreignkey",
        )
        batch_op.drop_column("default_runner_id")
