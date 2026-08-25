"""add directory repositories

Revision ID: b2d6f4a8c130
Revises: a4c9e2f6b731
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d6f4a8c130"
down_revision = "a4c9e2f6b731"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("repository") as batch_op:
        batch_op.add_column(
            sa.Column(
                "repository_type",
                sa.String(length=16),
                nullable=False,
                server_default="git",
            )
        )
        batch_op.add_column(
            sa.Column(
                "directory_path",
                sa.String(length=1000),
                nullable=False,
                server_default="",
            )
        )
    with op.batch_alter_table("repository") as batch_op:
        batch_op.alter_column("repository_type", server_default=None)
        batch_op.alter_column("directory_path", server_default=None)


def downgrade():
    with op.batch_alter_table("repository") as batch_op:
        batch_op.drop_column("directory_path")
        batch_op.drop_column("repository_type")
