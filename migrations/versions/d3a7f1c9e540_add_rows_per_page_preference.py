"""add rows per page preference

Revision ID: d3a7f1c9e540
Revises: c8f1d2e4a690
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "d3a7f1c9e540"
down_revision = "c8f1d2e4a690"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(sa.Column("rows_per_page", sa.Integer(), nullable=False, server_default="50"))


def downgrade():
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("rows_per_page")
