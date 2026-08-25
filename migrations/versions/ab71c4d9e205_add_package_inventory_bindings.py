"""add package inventory bindings

Revision ID: ab71c4d9e205
Revises: 5d9f3b7a2c40
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa


revision = "ab71c4d9e205"
down_revision = "5d9f3b7a2c40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_package_input") as batch_op:
        batch_op.add_column(
            sa.Column(
                "bind_to_inventory",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "inventory_binding_name",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )

    with op.batch_alter_table("job_package_snapshot") as batch_op:
        batch_op.add_column(
            sa.Column(
                "inventory_bindings_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade():
    with op.batch_alter_table("job_package_snapshot") as batch_op:
        batch_op.drop_column("inventory_bindings_json")

    with op.batch_alter_table("project_package_input") as batch_op:
        batch_op.drop_column("inventory_binding_name")
        batch_op.drop_column("bind_to_inventory")
