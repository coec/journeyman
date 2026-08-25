"""add Job cancellation request timestamp

Revision ID: fe7b3d1a9c24
Revises: fd6a2c4e8b15
"""

from alembic import op
import sqlalchemy as sa

revision = "fe7b3d1a9c24"
down_revision = "fd6a2c4e8b15"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_job_cancel_requested_at", ["cancel_requested_at"], unique=False
        )

    # Existing rows stuck in cancelling pre-date this timestamp.  Their start/
    # queue time is deliberately used as a conservative lower bound; recovery
    # still refuses to finalise them while a runner reports active execution.
    op.execute(
        sa.text(
            "UPDATE job "
            "SET cancel_requested_at = COALESCE(started_at, queued_at, CURRENT_TIMESTAMP) "
            "WHERE status = 'cancelling' AND cancel_requested_at IS NULL"
        )
    )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_index("ix_job_cancel_requested_at")
        batch_op.drop_column("cancel_requested_at")
