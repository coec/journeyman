"""record requester for runner Environment synchronization

Revision ID: 8b4d6f0a2c31
Revises: 7a3c5e9f1b20
"""
from alembic import op
import sqlalchemy as sa

revision = "8b4d6f0a2c31"
down_revision = "7a3c5e9f1b20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner_environment_sync") as batch_op:
        batch_op.add_column(
            sa.Column(
                "requested_by",
                sa.String(length=255),
                nullable=False,
                server_default="system",
            )
        )
        batch_op.create_index(
            "ix_runner_environment_sync_requested_by",
            ["requested_by"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("runner_environment_sync") as batch_op:
        batch_op.drop_index("ix_runner_environment_sync_requested_by")
        batch_op.drop_column("requested_by")
