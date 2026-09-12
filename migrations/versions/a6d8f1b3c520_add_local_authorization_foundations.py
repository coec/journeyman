"""add local authorization foundations

Revision ID: a6d8f1b3c520
Revises: 9c5e7a1b3d42
"""
from alembic import op
import sqlalchemy as sa

revision = "a6d8f1b3c520"
down_revision = "9c5e7a1b3d42"
branch_labels = None
depends_on = None


BUILTIN_ROLES = (
    ("Admin", "Administer Journeyman configuration and authorization."),
    ("User", "Use Journeyman capabilities explicitly granted to the account."),
    ("Auditor", "Read-only audit and governance access."),
)

BUILTIN_RIGHTS = (
    ("Reviewer", "Perform technical reviews of submitted automation."),
    ("Approver", "Perform management approval of reviewed automation."),
)


def upgrade():
    op.create_table(
        "authorization_role",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_authorization_role_name"),
    )
    op.create_table(
        "authorization_right",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_authorization_right_name"),
    )
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("directory_object_guid", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("directory_object_guid", name="uq_user_account_directory_object_guid"),
        sa.UniqueConstraint("username", name="uq_user_account_username"),
    )
    op.create_index(op.f("ix_user_account_username"), "user_account", ["username"], unique=False)
    op.create_index(
        op.f("ix_user_account_directory_object_guid"),
        "user_account",
        ["directory_object_guid"],
        unique=False,
    )
    op.create_table(
        "user_account_role",
        sa.Column("user_account_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["authorization_role.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_account_id", "role_id"),
    )
    op.create_table(
        "user_account_right",
        sa.Column("user_account_id", sa.Integer(), nullable=False),
        sa.Column("right_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["right_id"], ["authorization_right.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_account_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_account_id", "right_id"),
    )

    role_table = sa.table(
        "authorization_role",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("builtin", sa.Boolean),
    )
    right_table = sa.table(
        "authorization_right",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("builtin", sa.Boolean),
    )
    op.bulk_insert(
        role_table,
        [{"name": name, "description": description, "builtin": True} for name, description in BUILTIN_ROLES],
    )
    op.bulk_insert(
        right_table,
        [{"name": name, "description": description, "builtin": True} for name, description in BUILTIN_RIGHTS],
    )


def downgrade():
    op.drop_table("user_account_right")
    op.drop_table("user_account_role")
    op.drop_index(op.f("ix_user_account_directory_object_guid"), table_name="user_account")
    op.drop_index(op.f("ix_user_account_username"), table_name="user_account")
    op.drop_table("user_account")
    op.drop_table("authorization_right")
    op.drop_table("authorization_role")
