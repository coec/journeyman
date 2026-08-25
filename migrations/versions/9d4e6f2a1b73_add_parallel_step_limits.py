"""add parallel step limits

Revision ID: 9d4e6f2a1b73
Revises: 8c2e7a4d9b61
"""

from alembic import op
import sqlalchemy as sa

revision = "9d4e6f2a1b73"
down_revision = "8c2e7a4d9b61"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_parallel_steps",
                sa.Integer(),
                nullable=False,
                server_default="4",
            )
        )

    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_parallel_steps",
                sa.Integer(),
                nullable=False,
                server_default="4",
            )
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_column("max_parallel_steps")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("max_parallel_steps")
