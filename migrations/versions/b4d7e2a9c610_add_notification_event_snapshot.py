"""add immutable notification event snapshot

Revision ID: b4d7e2a9c610
Revises: a1c4e7f9b203
"""

from alembic import op
import sqlalchemy as sa

revision = "b4d7e2a9c610"
down_revision = "a1c4e7f9b203"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("notification_event") as batch:
        batch.add_column(
            sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="")
        )


def downgrade():
    with op.batch_alter_table("notification_event") as batch:
        batch.drop_column("snapshot_json")
