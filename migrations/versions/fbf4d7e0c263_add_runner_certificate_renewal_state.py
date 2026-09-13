"""add runner certificate renewal state

Revision ID: fbf4d7e0c263
Revises: eaf3c6d9b152
"""

from alembic import op
import sqlalchemy as sa

revision = "fbf4d7e0c263"
down_revision = "eaf3c6d9b152"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runner",
        sa.Column("pki_renewal_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runner",
        sa.Column("pki_renewal_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "runner",
        sa.Column("pki_renewal_last_error", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "runner",
        sa.Column("pki_renewal_warning_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("runner") as batch_op:
        batch_op.alter_column("pki_renewal_failure_count", server_default=None)
        batch_op.alter_column("pki_renewal_last_error", server_default=None)


def downgrade():
    op.drop_column("runner", "pki_renewal_warning_at")
    op.drop_column("runner", "pki_renewal_last_error")
    op.drop_column("runner", "pki_renewal_failure_count")
    op.drop_column("runner", "pki_renewal_last_attempt_at")
