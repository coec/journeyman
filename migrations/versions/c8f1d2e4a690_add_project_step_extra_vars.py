"""add per-step extra variables

Revision ID: c8f1d2e4a690
Revises: c3e7a1d9f520
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "c8f1d2e4a690"
down_revision = "c3e7a1d9f520"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch:
        batch.add_column(
            sa.Column(
                "extra_vars_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )

    with op.batch_alter_table("job_step") as batch:
        batch.add_column(
            sa.Column(
                "extra_vars_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch:
        batch.drop_column("extra_vars_json")

    with op.batch_alter_table("project_step") as batch:
        batch.drop_column("extra_vars_json")
