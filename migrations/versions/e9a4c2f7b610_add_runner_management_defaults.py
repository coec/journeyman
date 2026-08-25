"""add runner management credential defaults

Revision ID: e9a4c2f7b610
Revises: d4f8b2a1c690
"""
from alembic import op
import sqlalchemy as sa

revision = "e9a4c2f7b610"
down_revision = "d4f8b2a1c690"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(
            sa.Column("management_bootstrap_credential_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "management_pip_proxy_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("management_pip_proxy_credential_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            "ix_runner_management_bootstrap_credential_id",
            ["management_bootstrap_credential_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_runner_management_pip_proxy_credential_id",
            ["management_pip_proxy_credential_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_runner_management_bootstrap_credential_id_credential",
            "credential",
            ["management_bootstrap_credential_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_runner_management_pip_proxy_credential_id_credential",
            "credential",
            ["management_pip_proxy_credential_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_constraint(
            "fk_runner_management_pip_proxy_credential_id_credential",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_runner_management_bootstrap_credential_id_credential",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_runner_management_pip_proxy_credential_id")
        batch_op.drop_index("ix_runner_management_bootstrap_credential_id")
        batch_op.drop_column("management_pip_proxy_credential_id")
        batch_op.drop_column("management_pip_proxy_required")
        batch_op.drop_column("management_bootstrap_credential_id")
