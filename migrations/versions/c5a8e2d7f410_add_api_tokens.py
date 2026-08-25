"""add API tokens

Revision ID: c5a8e2d7f410
Revises: b4d7e2a9c610
"""
from alembic import op
import sqlalchemy as sa

revision = "c5a8e2d7f410"
down_revision = "b4d7e2a9c610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index("ix_api_token_username", "api_token", ["username"], unique=False)
    op.create_index("ix_api_token_token_digest", "api_token", ["token_digest"], unique=True)


def downgrade():
    op.drop_index("ix_api_token_token_digest", table_name="api_token")
    op.drop_index("ix_api_token_username", table_name="api_token")
    op.drop_table("api_token")
