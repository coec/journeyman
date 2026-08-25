"""add runner managed capabilities

Revision ID: f2a6c8d4e190
Revises: e7c2b91a4d65
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a6c8d4e190"
down_revision = "e7c2b91a4d65"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(
            sa.Column(
                "managed_capabilities_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_column("managed_capabilities_json")
