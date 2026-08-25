"""add local runner status

Revision ID: a4d8f2c7b619
Revises: f3a7c9d1e205
"""
from alembic import op
import sqlalchemy as sa

revision = "a4d8f2c7b619"
down_revision = "f3a7c9d1e205"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(
            sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index("ix_runner_is_local", ["is_local"])


def downgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_index("ix_runner_is_local")
        batch_op.drop_column("is_local")
