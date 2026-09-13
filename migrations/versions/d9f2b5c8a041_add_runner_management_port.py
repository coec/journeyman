"""add runner management port

Revision ID: d9f2b5c8a041
Revises: c8e4f1a7b930
"""

from alembic import op
import sqlalchemy as sa

revision = "d9f2b5c8a041"
down_revision = "c8e4f1a7b930"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runner",
        sa.Column("management_port", sa.Integer(), nullable=False, server_default="8443"),
    )
    with op.batch_alter_table("runner") as batch_op:
        batch_op.alter_column("management_port", server_default=None)


def downgrade():
    op.drop_column("runner", "management_port")
