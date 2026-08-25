"""add project runner routing

Revision ID: e7b4c2d9a610
Revises: d2f6a8c1e407
Create Date: 2026-08-07 09:36:00
"""

from alembic import op
import sqlalchemy as sa

revision = "e7b4c2d9a610"
down_revision = "d2f6a8c1e407"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column("runner_routing", sa.String(length=24), nullable=False, server_default="local")
        )
        batch_op.add_column(
            sa.Column("runner_site", sa.String(length=120), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("runner_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_project_runner_id", ["runner_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_project_runner_id_runner", "runner", ["runner_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(sa.Column("required_runner_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_job_required_runner_id", ["required_runner_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_job_required_runner_id_runner", "runner", ["required_runner_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_constraint("fk_job_required_runner_id_runner", type_="foreignkey")
        batch_op.drop_index("ix_job_required_runner_id")
        batch_op.drop_column("required_runner_id")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint("fk_project_runner_id_runner", type_="foreignkey")
        batch_op.drop_index("ix_project_runner_id")
        batch_op.drop_column("runner_id")
        batch_op.drop_column("runner_site")
        batch_op.drop_column("runner_routing")
