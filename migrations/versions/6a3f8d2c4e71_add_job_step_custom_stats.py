"""add job step custom stats

Revision ID: 6a3f8d2c4e71
Revises: 5e2a9c7d1f40
Create Date: 2026-08-06 14:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "6a3f8d2c4e71"
down_revision = "5e2a9c7d1f40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "custom_stats_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("custom_stats_json")
