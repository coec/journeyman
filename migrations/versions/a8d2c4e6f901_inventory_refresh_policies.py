"""Three-state inventory refresh policies.

Revision ID: a8d2c4e6f901
Revises: a3f5b81c9d72
"""
from alembic import op
import sqlalchemy as sa

revision = "a8d2c4e6f901"
down_revision = "e5a9d3b7c124"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("inventory_refresh_policy", sa.String(16),
                                   nullable=False, server_default="if_missing"))
    for table in ("project_step", "job_step"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("inventory_refresh_after_policy", sa.String(16),
                                       nullable=False, server_default="never"))
        op.execute(sa.text(
            "UPDATE {} SET inventory_refresh_after_policy = 'always' "
            "WHERE refresh_inventory_after = 1".format(table)
        ))


def downgrade():
    for table in ("job_step", "project_step"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("inventory_refresh_after_policy")
    with op.batch_alter_table("project") as batch:
        batch.drop_column("inventory_refresh_policy")
