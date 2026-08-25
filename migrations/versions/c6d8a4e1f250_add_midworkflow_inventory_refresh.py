"""add mid-workflow inventory refresh

Revision ID: c6d8a4e1f250
Revises: b3c7e1f4a902
"""

from alembic import op
import sqlalchemy as sa


revision = "c6d8a4e1f250"
down_revision = "b3c7e1f4a902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "refresh_inventory_after",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "refresh_inventory_after",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column(
            "refresh_inventory_after"
        )

    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column(
            "refresh_inventory_after"
        )
