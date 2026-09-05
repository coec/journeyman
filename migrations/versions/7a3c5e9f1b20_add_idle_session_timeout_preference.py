"""add idle session timeout preference

Revision ID: 7a3c5e9f1b20
Revises: 6f2a9c4d8e10
"""
from alembic import op
import sqlalchemy as sa

revision = "7a3c5e9f1b20"
down_revision = "6f2a9c4d8e10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column(
                "idle_session_timeout_minutes",
                sa.Integer(),
                nullable=False,
                server_default="480",
            )
        )


def downgrade():
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("idle_session_timeout_minutes")
