"""add per-Project approval requirement

Revision ID: e6a8c0d2f507
Revises: d5f7b9c1e496
"""

from alembic import op
import sqlalchemy as sa


revision = "e6a8c0d2f507"
down_revision = "d5f7b9c1e496"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "approval_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("approval_required")
