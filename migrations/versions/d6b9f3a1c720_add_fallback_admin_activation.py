"""add fallback administrator activation lifecycle

Revision ID: d6b9f3a1c720
Revises: c5a8e2d7f410
"""
from alembic import op
import sqlalchemy as sa

revision = "d6b9f3a1c720"
down_revision = "c5a8e2d7f410"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fallback_admin_activation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_reason", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fallback_admin_activation_activated_at", "fallback_admin_activation", ["activated_at"], unique=False)
    op.create_index("ix_fallback_admin_activation_expires_at", "fallback_admin_activation", ["expires_at"], unique=False)
    op.create_index("ix_fallback_admin_activation_expired_at", "fallback_admin_activation", ["expired_at"], unique=False)


def downgrade():
    op.drop_index("ix_fallback_admin_activation_expired_at", table_name="fallback_admin_activation")
    op.drop_index("ix_fallback_admin_activation_expires_at", table_name="fallback_admin_activation")
    op.drop_index("ix_fallback_admin_activation_activated_at", table_name="fallback_admin_activation")
    op.drop_table("fallback_admin_activation")
