"""add shell project execution type

Revision ID: b7e3c1d4a920
Revises: 9d4e6f2a1b73
"""

from alembic import op
import sqlalchemy as sa

revision = "b7e3c1d4a920"
down_revision = "9d4e6f2a1b73"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_type",
                sa.String(length=20),
                nullable=False,
                server_default="ansible",
            )
        )

    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_type",
                sa.String(length=20),
                nullable=False,
                server_default="ansible",
            )
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_column("execution_type")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("execution_type")
