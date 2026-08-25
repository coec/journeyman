"""add system settings

Revision ID: 4d0f2a6b8c31
Revises: c91e4f7a2d30
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "4d0f2a6b8c31"
down_revision = "c91e4f7a2d30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_setting",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "public_fqdn",
            sa.String(length=253),
            nullable=False,
        ),
        sa.Column(
            "https_port",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tls_certificate_path",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "tls_private_key_path",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "tls_chain_path",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "redirect_http_to_https",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "apply_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "apply_message",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "applied_config_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "id = 1",
            name="ck_system_setting_singleton",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table(
        "system_setting"
    )
