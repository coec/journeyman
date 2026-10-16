"""add optional fixed Package-wide Ansible limit

Revision ID: 9f2c7e4b6d11
Revises: e5a9d3b7c124
"""
from alembic import op
import sqlalchemy as sa

revision = "9f2c7e4b6d11"
down_revision = "e5a9d3b7c124"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_package") as batch:
        batch.add_column(
            sa.Column("limit", sa.String(length=500), nullable=False,
                      server_default="")
        )


def downgrade():
    with op.batch_alter_table("project_package") as batch:
        batch.drop_column("limit")
