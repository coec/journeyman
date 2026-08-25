"""add ansible check mode to project and job steps

Revision ID: c1e4a7b9d230
Revises: b7d3e5f1a920
"""
from alembic import op
import sqlalchemy as sa

revision = "c1e4a7b9d230"
down_revision = "b7d3e5f1a920"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(sa.Column("check_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(sa.Column("check_mode", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("check_mode")
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("check_mode")
