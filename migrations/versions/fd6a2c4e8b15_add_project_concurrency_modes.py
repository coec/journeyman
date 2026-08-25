"""add project concurrency modes

Revision ID: fd6a2c4e8b15
Revises: fc5d9e3a7b12
"""

from alembic import op
import sqlalchemy as sa


revision = "fd6a2c4e8b15"
down_revision = "fc5d9e3a7b12"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "concurrency_policy",
                sa.String(length=32),
                nullable=False,
                server_default="unrestricted",
            )
        )

    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column(
                "concurrency_policy",
                sa.String(length=32),
                nullable=False,
                server_default="unrestricted",
            )
        )
        batch_op.add_column(
            sa.Column("concurrency_signature", sa.String(length=64), nullable=True)
        )
        batch_op.create_index(
            "ix_job_concurrency_signature", ["concurrency_signature"], unique=False
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_index("ix_job_concurrency_signature")
        batch_op.drop_column("concurrency_signature")
        batch_op.drop_column("concurrency_policy")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("concurrency_policy")
