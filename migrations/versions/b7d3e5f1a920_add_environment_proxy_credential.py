"""add environment proxy credential

Revision ID: b7d3e5f1a920
Revises: f4b8d2c6a901
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d3e5f1a920"
down_revision = "f4b8d2c6a901"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("environment") as batch_op:
        batch_op.add_column(sa.Column("proxy_credential_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_environment_proxy_credential_id", ["proxy_credential_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_environment_proxy_credential_id_credential",
            "credential",
            ["proxy_credential_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("environment") as batch_op:
        batch_op.drop_constraint("fk_environment_proxy_credential_id_credential", type_="foreignkey")
        batch_op.drop_index("ix_environment_proxy_credential_id")
        batch_op.drop_column("proxy_credential_id")
