"""add directory settings and teams

Revision ID: b18f4a6d2c90
Revises: 4d0f2a6b8c31
Create Date: 2026-08-05 16:32:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b18f4a6d2c90"
down_revision = "4d0f2a6b8c31"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "project_package_permission",
        sa.Column(
            "principal_object_guid",
            sa.String(length=36),
            nullable=True,
        ),
    )
    op.add_column(
        "project_package_permission",
        sa.Column(
            "principal_dn",
            sa.String(length=1000),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        op.f(
            "ix_project_package_permission_principal_object_guid"
        ),
        "project_package_permission",
        ["principal_object_guid"],
        unique=False,
    )

    op.create_table(
        "directory_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("base_dn", sa.String(length=500), nullable=False),
        sa.Column("user_search_base", sa.String(length=500), nullable=False),
        sa.Column("group_search_base", sa.String(length=500), nullable=False),
        sa.Column("bind_username", sa.String(length=500), nullable=False),
        sa.Column("encrypted_bind_password", sa.LargeBinary(), nullable=True),
        sa.Column("ca_certificate_path", sa.String(length=500), nullable=False),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("operation_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("administrator_group_name", sa.String(length=255), nullable=False),
        sa.Column("user_group_name", sa.String(length=255), nullable=False),
        sa.Column("include_nested_groups", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_directory_setting_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "directory_server",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("directory_setting_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=1000), nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["directory_setting_id"],
            ["directory_setting.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "directory_setting_id",
            "host",
            "port",
            name="uq_directory_server_setting_host_port",
        ),
        sa.UniqueConstraint(
            "directory_setting_id",
            "position",
            name="uq_directory_server_setting_position",
        ),
    )
    op.create_index(
        op.f("ix_directory_server_directory_setting_id"),
        "directory_server",
        ["directory_setting_id"],
        unique=False,
    )

    op.create_table(
        "team",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("object_guid", sa.String(length=36), nullable=False),
        sa.Column("distinguished_name", sa.String(length=1000), nullable=False),
        sa.Column("sam_account_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("distinguished_name"),
        sa.UniqueConstraint("object_guid"),
    )

def downgrade():
    op.drop_table("team")
    op.drop_index(
        op.f("ix_directory_server_directory_setting_id"),
        table_name="directory_server",
    )
    op.drop_table("directory_server")
    op.drop_table("directory_setting")
    op.drop_index(
        op.f(
            "ix_project_package_permission_principal_object_guid"
        ),
        table_name="project_package_permission",
    )
    op.drop_column(
        "project_package_permission",
        "principal_dn",
    )
    op.drop_column(
        "project_package_permission",
        "principal_object_guid",
    )
