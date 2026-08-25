"""link inventories to credentials

Revision ID: 524cc3c22a5f
Revises: fbd6e3c0c923
Create Date: 2026-08-01 07:31:01.568241

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '524cc3c22a5f'
down_revision = 'fbd6e3c0c923'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "inventory_source",
        schema=None,
    ) as batch_op:
        batch_op.create_index(
            "ix_inventory_source_credential_id",
            ["credential_id"],
            unique=False,
        )

        batch_op.create_foreign_key(
            "fk_inventory_source_credential_id",
            "credential",
            ["credential_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table(
        "inventory_source",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_inventory_source_credential_id",
            type_="foreignkey",
        )

        batch_op.drop_index(
            "ix_inventory_source_credential_id"
        )
