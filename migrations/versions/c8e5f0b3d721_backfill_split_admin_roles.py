"""backfill split admin roles

Revision ID: c8e5f0b3d721
Revises: b7d4e9a2c610
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e5f0b3d721"
down_revision = "b7d4e9a2c610"
branch_labels = None
depends_on = None


def upgrade():
    """Preserve the capabilities of accounts that predate the Admin split.

    Before v2 authorization was split, Admin covered platform, automation and
    resource administration. Existing Admin accounts therefore receive both
    new specialist admin roles once. Future Admin assignments do not imply
    either specialist role.
    """

    bind = op.get_bind()
    role = sa.table(
        "authorization_role",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
    )
    account_role = sa.table(
        "user_account_role",
        sa.column("user_account_id", sa.Integer),
        sa.column("role_id", sa.Integer),
    )

    role_ids = {
        row.name: row.id
        for row in bind.execute(
            sa.select(role.c.id, role.c.name).where(
                role.c.name.in_(["Admin", "Automation Admin", "Resource Admin"])
            )
        )
    }
    admin_id = role_ids.get("Admin")
    automation_id = role_ids.get("Automation Admin")
    resource_id = role_ids.get("Resource Admin")
    if not admin_id or not automation_id or not resource_id:
        return

    admin_user_ids = [
        row.user_account_id
        for row in bind.execute(
            sa.select(account_role.c.user_account_id).where(
                account_role.c.role_id == admin_id
            )
        )
    ]

    for user_id in admin_user_ids:
        existing = {
            row.role_id
            for row in bind.execute(
                sa.select(account_role.c.role_id).where(
                    account_role.c.user_account_id == user_id
                )
            )
        }
        for required_role_id in (automation_id, resource_id):
            if required_role_id not in existing:
                bind.execute(
                    account_role.insert().values(
                        user_account_id=user_id,
                        role_id=required_role_id,
                    )
                )


def downgrade():
    # Deliberately do not remove assignments. A downgrade cannot distinguish
    # migration-added grants from grants an administrator assigned explicitly.
    pass
