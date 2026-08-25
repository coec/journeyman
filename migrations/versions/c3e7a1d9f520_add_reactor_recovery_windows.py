"""add Reactor recovery windows

Revision ID: c3e7a1d9f520
Revises: b2d6f4a8c130
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "c3e7a1d9f520"
down_revision = "b2d6f4a8c130"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("reactor") as batch:
        batch.add_column(sa.Column("recovery_window_seconds", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("recovery_match_json", sa.Text(), nullable=False, server_default='{"all":[]}'))
        batch.add_column(sa.Column("recovery_correlation_inputs_json", sa.Text(), nullable=False, server_default="[]"))
    with op.batch_alter_table("reaction") as batch:
        batch.add_column(sa.Column("execute_after", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("recovery_signal_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_reaction_execute_after", ["execute_after"], unique=False)
        batch.create_index("ix_reaction_recovery_signal_id", ["recovery_signal_id"], unique=False)
        batch.create_foreign_key("fk_reaction_recovery_signal_id_signal", "signal", ["recovery_signal_id"], ["id"], ondelete="SET NULL")

def downgrade():
    with op.batch_alter_table("reaction") as batch:
        batch.drop_constraint("fk_reaction_recovery_signal_id_signal", type_="foreignkey")
        batch.drop_index("ix_reaction_recovery_signal_id")
        batch.drop_index("ix_reaction_execute_after")
        batch.drop_column("suppressed_at")
        batch.drop_column("recovery_signal_id")
        batch.drop_column("execute_after")
    with op.batch_alter_table("reactor") as batch:
        batch.drop_column("recovery_correlation_inputs_json")
        batch.drop_column("recovery_match_json")
        batch.drop_column("recovery_window_seconds")
