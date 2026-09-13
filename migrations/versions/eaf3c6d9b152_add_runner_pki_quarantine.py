"""add runner PKI quarantine state

Revision ID: eaf3c6d9b152
Revises: d9f2b5c8a041
"""

from alembic import op
import sqlalchemy as sa

revision = "eaf3c6d9b152"
down_revision = "d9f2b5c8a041"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runner",
        sa.Column("pki_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runner",
        sa.Column("pki_quarantine_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "runner",
        sa.Column("pki_quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("runner") as batch_op:
        batch_op.alter_column("pki_quarantined", server_default=None)
        batch_op.alter_column("pki_quarantine_reason", server_default=None)


def downgrade():
    op.drop_column("runner", "pki_quarantined_at")
    op.drop_column("runner", "pki_quarantine_reason")
    op.drop_column("runner", "pki_quarantined")
