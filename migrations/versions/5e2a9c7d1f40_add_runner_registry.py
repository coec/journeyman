"""add runner registry

Revision ID: 5e2a9c7d1f40
Revises: 1c8d7f4a2b90
"""

from alembic import op
import sqlalchemy as sa

revision = "5e2a9c7d1f40"
down_revision = "1c8d7f4a2b90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runner",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("runner_uuid", sa.String(length=36), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("site", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_concurrent_steps", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("registration_token_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("api_secret_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("running_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("free_workspace_bytes", sa.BigInteger(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("runner_uuid"),
    )
    op.create_index("ix_runner_runner_uuid", "runner", ["runner_uuid"], unique=True)
    op.create_index("ix_runner_last_heartbeat_at", "runner", ["last_heartbeat_at"], unique=False)


def downgrade():
    op.drop_index("ix_runner_last_heartbeat_at", table_name="runner")
    op.drop_index("ix_runner_runner_uuid", table_name="runner")
    op.drop_table("runner")
