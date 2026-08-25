"""add auth session directory revalidation timestamp

Revision ID: e4b7c9a1d205
Revises: d9a1e4c7b632
"""

from alembic import op
import sqlalchemy as sa


revision = "e4b7c9a1d205"
down_revision = "d9a1e4c7b632"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("auth_session") as batch_op:
        batch_op.add_column(
            sa.Column("directory_checked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_auth_session_directory_checked_at",
            ["directory_checked_at"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("auth_session") as batch_op:
        batch_op.drop_index("ix_auth_session_directory_checked_at")
        batch_op.drop_column("directory_checked_at")
