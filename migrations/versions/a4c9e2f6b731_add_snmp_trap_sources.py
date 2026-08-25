"""add SNMP Trap Sources

Revision ID: a4c9e2f6b731
Revises: f2a6c8d4e190
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "a4c9e2f6b731"
down_revision = "f2a6c8d4e190"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("signal_source") as batch_op:
        batch_op.add_column(
            sa.Column("snmp_port", sa.Integer(), nullable=False, server_default="162")
        )


def downgrade():
    with op.batch_alter_table("signal_source") as batch_op:
        batch_op.drop_column("snmp_port")
