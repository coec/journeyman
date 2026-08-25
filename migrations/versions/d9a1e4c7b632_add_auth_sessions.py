"""add server-side authenticated browser session registry

Revision ID: d9a1e4c7b632
Revises: c8e4a1f6b320
"""

from alembic import op
import sqlalchemy as sa


revision = "d9a1e4c7b632"
down_revision = "c8e4a1f6b320"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_session",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("user_object_guid", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_auth_session_username", "auth_session", ["username"])
    op.create_index("ix_auth_session_user_object_guid", "auth_session", ["user_object_guid"])
    op.create_index("ix_auth_session_created_at", "auth_session", ["created_at"])
    op.create_index("ix_auth_session_last_seen_at", "auth_session", ["last_seen_at"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])
    op.create_index("ix_auth_session_revoked_at", "auth_session", ["revoked_at"])


def downgrade():
    op.drop_index("ix_auth_session_revoked_at", table_name="auth_session")
    op.drop_index("ix_auth_session_expires_at", table_name="auth_session")
    op.drop_index("ix_auth_session_last_seen_at", table_name="auth_session")
    op.drop_index("ix_auth_session_created_at", table_name="auth_session")
    op.drop_index("ix_auth_session_user_object_guid", table_name="auth_session")
    op.drop_index("ix_auth_session_username", table_name="auth_session")
    op.drop_table("auth_session")
