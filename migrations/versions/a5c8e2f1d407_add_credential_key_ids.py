"""add credential key ids

Revision ID: a5c8e2f1d407
Revises: e4b7c9a1d205
"""

from alembic import op
import sqlalchemy as sa

revision = "a5c8e2f1d407"
down_revision = "e4b7c9a1d205"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("credential") as batch_op:
        batch_op.add_column(sa.Column("credential_key_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_credential_credential_key_id", ["credential_key_id"], unique=False)
    with op.batch_alter_table("job_credential_snapshot") as batch_op:
        batch_op.add_column(sa.Column("credential_key_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_job_credential_snapshot_credential_key_id", ["credential_key_id"], unique=False)


def downgrade():
    with op.batch_alter_table("job_credential_snapshot") as batch_op:
        batch_op.drop_index("ix_job_credential_snapshot_credential_key_id")
        batch_op.drop_column("credential_key_id")
    with op.batch_alter_table("credential") as batch_op:
        batch_op.drop_index("ix_credential_credential_key_id")
        batch_op.drop_column("credential_key_id")
