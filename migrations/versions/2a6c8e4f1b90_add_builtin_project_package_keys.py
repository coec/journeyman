"""add builtin project/package keys

Revision ID: 2a6c8e4f1b90
Revises: 0f4c8d2a6b91
"""

from alembic import op
import sqlalchemy as sa


revision = "2a6c8e4f1b90"
down_revision = "0f4c8d2a6b91"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("builtin_key", sa.String(length=120), nullable=True))
        batch_op.create_index("ix_project_builtin_key", ["builtin_key"], unique=True)

    with op.batch_alter_table("project_package") as batch_op:
        batch_op.add_column(sa.Column("builtin_key", sa.String(length=120), nullable=True))
        batch_op.create_index("ix_project_package_builtin_key", ["builtin_key"], unique=True)


def downgrade():
    with op.batch_alter_table("project_package") as batch_op:
        batch_op.drop_index("ix_project_package_builtin_key")
        batch_op.drop_column("builtin_key")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_index("ix_project_builtin_key")
        batch_op.drop_column("builtin_key")
