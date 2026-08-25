"""add runner crews and load metrics

Revision ID: c8e4a1f6b320
Revises: ab71c4d9e205
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa


revision = "c8e4a1f6b320"
down_revision = "ab71c4d9e205"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runner_crew",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "runner_crew_member",
        sa.Column("runner_crew_id", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["runner_crew_id"], ["runner_crew.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runner_id"], ["runner.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("runner_crew_id", "runner_id"),
    )

    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(sa.Column("load_average_1m", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("load_average_5m", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("cpu_count", sa.Integer(), nullable=True))

    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("default_runner_crew_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_project_default_runner_crew_id", ["default_runner_crew_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_project_default_runner_crew_id_runner_crew",
            "runner_crew",
            ["default_runner_crew_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(sa.Column("default_runner_crew_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_job_default_runner_crew_id", ["default_runner_crew_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_job_default_runner_crew_id_runner_crew",
            "runner_crew",
            ["default_runner_crew_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_constraint("fk_job_default_runner_crew_id_runner_crew", type_="foreignkey")
        batch_op.drop_index("ix_job_default_runner_crew_id")
        batch_op.drop_column("default_runner_crew_id")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint("fk_project_default_runner_crew_id_runner_crew", type_="foreignkey")
        batch_op.drop_index("ix_project_default_runner_crew_id")
        batch_op.drop_column("default_runner_crew_id")

    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_column("cpu_count")
        batch_op.drop_column("load_average_5m")
        batch_op.drop_column("load_average_1m")

    op.drop_table("runner_crew_member")
    op.drop_table("runner_crew")
