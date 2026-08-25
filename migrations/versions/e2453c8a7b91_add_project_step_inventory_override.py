"""add project step inventory override

Revision ID: e2453c8a7b91
Revises: d71f2a9c4b10
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa


revision = "e2453c8a7b91"
down_revision = "d71f2a9c4b10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "project_step",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "inventory_id",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.create_index(
            "ix_project_step_inventory_id",
            ["inventory_id"],
            unique=False,
        )

        batch_op.create_foreign_key(
            "fk_project_step_inventory_id_inventory_source",
            "inventory_source",
            ["inventory_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table(
        "project_step",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_project_step_inventory_id_inventory_source",
            type_="foreignkey",
        )

        batch_op.drop_index(
            "ix_project_step_inventory_id"
        )

        batch_op.drop_column(
            "inventory_id"
        )
