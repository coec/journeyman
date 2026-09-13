"""add Package schedule targets

Revision ID: d3e7b1c5a902
Revises: c2d6a9f1e840
"""

from alembic import op
import sqlalchemy as sa

revision = "d3e7b1c5a902"
down_revision = "c2d6a9f1e840"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_schedule") as batch_op:
        batch_op.alter_column(
            "project_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(sa.Column("package_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_project_schedule_package_id",
            "project_package",
            ["package_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_project_schedule_package_id",
            ["package_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_project_schedule_package_name",
            ["package_id", "name"],
        )
        batch_op.create_check_constraint(
            "ck_project_schedule_one_target",
            "(project_id IS NOT NULL AND package_id IS NULL) OR "
            "(project_id IS NULL AND package_id IS NOT NULL)",
        )


def downgrade():
    # Package-targeted schedules cannot be represented by the old schema.
    op.execute("DELETE FROM project_schedule WHERE package_id IS NOT NULL")
    with op.batch_alter_table("project_schedule") as batch_op:
        batch_op.drop_constraint("ck_project_schedule_one_target", type_="check")
        batch_op.drop_constraint("uq_project_schedule_package_name", type_="unique")
        batch_op.drop_index("ix_project_schedule_package_id")
        batch_op.drop_constraint("fk_project_schedule_package_id", type_="foreignkey")
        batch_op.drop_column("package_id")
        batch_op.alter_column(
            "project_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
