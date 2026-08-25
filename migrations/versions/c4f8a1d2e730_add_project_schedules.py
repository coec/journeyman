"""add project schedules

Revision ID: c4f8a1d2e730
Revises: b7e3c1d4a920
"""

from alembic import op
import sqlalchemy as sa

revision = "c4f8a1d2e730"
down_revision = "b7e3c1d4a920"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_schedule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("schedule_type", sa.String(length=20), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("weekdays", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_job_id"], ["job.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_project_schedule_project_name"),
    )
    op.create_index(op.f("ix_project_schedule_project_id"), "project_schedule", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_schedule_enabled"), "project_schedule", ["enabled"], unique=False)
    op.create_index(op.f("ix_project_schedule_next_run_at"), "project_schedule", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_project_schedule_claimed_at"), "project_schedule", ["claimed_at"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_project_schedule_claimed_at"), table_name="project_schedule")
    op.drop_index(op.f("ix_project_schedule_next_run_at"), table_name="project_schedule")
    op.drop_index(op.f("ix_project_schedule_enabled"), table_name="project_schedule")
    op.drop_index(op.f("ix_project_schedule_project_id"), table_name="project_schedule")
    op.drop_table("project_schedule")
