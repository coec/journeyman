"""add remote runner dispatch

Revision ID: f3a7c9d1e205
Revises: e6a1b3c5d7f9
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a7c9d1e205"
down_revision = "e6a1b3c5d7f9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"))
    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(sa.Column("dispatch_target", sa.String(length=20), nullable=False, server_default="local"))
        batch_op.add_column(sa.Column("required_runner_site", sa.String(length=120), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("required_runner_capabilities_json", sa.Text(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("assigned_runner_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("dispatch_token", sa.String(length=64), nullable=False, server_default=""))
        batch_op.create_foreign_key("fk_job_assigned_runner_id_runner", "runner", ["assigned_runner_id"], ["id"])
        batch_op.create_index("ix_job_dispatch_target", ["dispatch_target"])
        batch_op.create_index("ix_job_assigned_runner_id", ["assigned_runner_id"])
        batch_op.create_index("ix_job_dispatch_token", ["dispatch_token"])


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_index("ix_job_dispatch_token")
        batch_op.drop_index("ix_job_assigned_runner_id")
        batch_op.drop_index("ix_job_dispatch_target")
        batch_op.drop_constraint("fk_job_assigned_runner_id_runner", type_="foreignkey")
        batch_op.drop_column("dispatch_token")
        batch_op.drop_column("assigned_at")
        batch_op.drop_column("assigned_runner_id")
        batch_op.drop_column("required_runner_capabilities_json")
        batch_op.drop_column("required_runner_site")
        batch_op.drop_column("dispatch_target")
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_column("capabilities_json")
