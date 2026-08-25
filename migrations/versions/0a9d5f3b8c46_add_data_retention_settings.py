"""add configurable Job and Reaction retention settings

Revision ID: 0a9d5f3b8c46
Revises: ff8c4e2a7d35
"""

from alembic import op
import sqlalchemy as sa

revision = "0a9d5f3b8c46"
down_revision = "ff8c4e2a7d35"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("system_setting") as batch:
        batch.add_column(
            sa.Column(
                "job_retention_days",
                sa.Integer(),
                nullable=False,
                server_default="180",
            )
        )
        batch.add_column(
            sa.Column(
                "reaction_retention_days",
                sa.Integer(),
                nullable=False,
                server_default="180",
            )
        )


def downgrade():
    with op.batch_alter_table("system_setting") as batch:
        batch.drop_column("reaction_retention_days")
        batch.drop_column("job_retention_days")
