"""add runner provenance to job step host results

Revision ID: 4c8e2a7d6b31
Revises: 3b7d9f1a5c20
"""

from alembic import op
import sqlalchemy as sa


revision = "4c8e2a7d6b31"
down_revision = "3b7d9f1a5c20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job_step_host_result") as batch_op:
        batch_op.add_column(
            sa.Column("runner_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "runner_name",
                sa.String(length=120),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "runner_hostname",
                sa.String(length=255),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "runner_local",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_foreign_key(
            "fk_job_step_host_result_runner_id",
            "runner",
            ["runner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_job_step_host_result_runner_id",
            ["runner_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("job_step_host_result") as batch_op:
        batch_op.drop_index("ix_job_step_host_result_runner_id")
        batch_op.drop_constraint(
            "fk_job_step_host_result_runner_id",
            type_="foreignkey",
        )
        batch_op.drop_column("runner_local")
        batch_op.drop_column("runner_hostname")
        batch_op.drop_column("runner_name")
        batch_op.drop_column("runner_id")

