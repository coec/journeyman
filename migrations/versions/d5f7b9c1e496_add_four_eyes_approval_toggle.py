"""add system-wide 4-eyes approval toggle

Revision ID: d5f7b9c1e496
Revises: c4e6a8b0d385
Create Date: 2026-09-12 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d5f7b9c1e496"
down_revision = "c4e6a8b0d385"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("system_setting") as batch_op:
        batch_op.add_column(
            sa.Column(
                "four_eyes_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("system_setting") as batch_op:
        batch_op.drop_column("four_eyes_enabled")
