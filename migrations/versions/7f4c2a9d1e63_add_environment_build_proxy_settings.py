"""add environment build proxy settings

Revision ID: 7f4c2a9d1e63
Revises: 62c4a7e91bd0
"""
from alembic import op
import sqlalchemy as sa

revision = "7f4c2a9d1e63"
down_revision = "62c4a7e91bd0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "environment_build_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proxy_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("proxy_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("proxy_username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("encrypted_proxy_password", sa.LargeBinary(), nullable=True),
        sa.Column("no_proxy", sa.Text(), nullable=False, server_default="localhost,127.0.0.1"),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_environment_build_setting_singleton"),
    )


def downgrade():
    op.drop_table("environment_build_setting")
