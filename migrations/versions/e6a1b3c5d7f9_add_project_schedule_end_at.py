"""add project schedule end date and time

Revision ID: e6a1b3c5d7f9
Revises: c4f8a1d2e730
"""

from alembic import op
import sqlalchemy as sa

revision = "e6a1b3c5d7f9"
down_revision = "c4f8a1d2e730"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "project_schedule",
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("project_schedule", "end_at")
