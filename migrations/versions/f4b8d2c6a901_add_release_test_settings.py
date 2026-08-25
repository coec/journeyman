"""add release test settings

Revision ID: f4b8d2c6a901
Revises: e8c4a1d7f620
"""

from alembic import op
import sqlalchemy as sa


revision = "f4b8d2c6a901"
down_revision = "e8c4a1d7f620"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_test_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=True),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("runner_crew_id", sa.Integer(), nullable=True),
        sa.Column("host_pattern", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("alternate_become_users", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_release_test_setting_singleton"),
        sa.ForeignKeyConstraint(["credential_id"], ["credential.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["inventory_id"], ["inventory_source.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runner_crew_id"], ["runner_crew.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("release_test_setting") as batch_op:
        batch_op.create_index("ix_release_test_setting_inventory_id", ["inventory_id"], unique=False)
        batch_op.create_index("ix_release_test_setting_credential_id", ["credential_id"], unique=False)
        batch_op.create_index("ix_release_test_setting_runner_crew_id", ["runner_crew_id"], unique=False)


def downgrade():
    with op.batch_alter_table("release_test_setting") as batch_op:
        batch_op.drop_index("ix_release_test_setting_runner_crew_id")
        batch_op.drop_index("ix_release_test_setting_credential_id")
        batch_op.drop_index("ix_release_test_setting_inventory_id")
    op.drop_table("release_test_setting")
