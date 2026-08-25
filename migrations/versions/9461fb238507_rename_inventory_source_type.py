"""rename inventory source type

Revision ID: 9461fb238507
Revises: 524cc3c22a5f
Create Date: 2026-08-03 17:49:19.287358

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9461fb238507'
down_revision = '524cc3c22a5f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "inventory_source",
        schema=None,
    ) as batch_op:
        batch_op.alter_column(
            "source_type",
            new_column_name="inventory_type",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table(
        "inventory_source",
        schema=None,
    ) as batch_op:
        batch_op.alter_column(
            "inventory_type",
            new_column_name="source_type",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )
