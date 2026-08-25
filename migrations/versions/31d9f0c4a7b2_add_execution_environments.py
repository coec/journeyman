"""add execution environments

Revision ID: 31d9f0c4a7b2
Revises: 8a71c4d2e930
"""
from alembic import op
import sqlalchemy as sa

revision = "31d9f0c4a7b2"
down_revision = "8a71c4d2e930"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "environment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("python_version", sa.String(length=120), nullable=False),
        sa.Column("ansible_version", sa.String(length=120), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_message", sa.Text(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("path"),
    )
    op.create_index(op.f("ix_environment_is_default"), "environment", ["is_default"], unique=False)
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(sa.Column("environment_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_project_step_environment_id"), ["environment_id"], unique=False)
        batch_op.create_foreign_key("fk_project_step_environment_id", "environment", ["environment_id"], ["id"], ondelete="RESTRICT")
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(sa.Column("environment_name", sa.String(length=120), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("environment_path", sa.String(length=1000), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("environment_path")
        batch_op.drop_column("environment_name")
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_constraint("fk_project_step_environment_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_project_step_environment_id"))
        batch_op.drop_column("environment_id")
    op.drop_index(op.f("ix_environment_is_default"), table_name="environment")
    op.drop_table("environment")
