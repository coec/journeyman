"""add failure-only workflow steps

Revision ID: d7e9b5f2a361
Revises: c6d8a4e1f250
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e9b5f2a361"
down_revision = "c6d8a4e1f250"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "failure_only",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "failure_only",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("failure_only")

    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("failure_only")
