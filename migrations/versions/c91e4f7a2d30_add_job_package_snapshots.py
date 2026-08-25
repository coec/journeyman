"""add job package snapshots

Revision ID: c91e4f7a2d30
Revises: a7c4d9e2f611
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa


revision = "c91e4f7a2d30"
down_revision = "a7c4d9e2f611"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_package_snapshot",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "package_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "package_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "package_owner",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "package_definition_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "package_definition_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "display_values_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "operational_targets_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "encrypted_extra_vars",
            sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column(
            "extra_vars_format_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "step_limit",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["project_package.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            name=(
                "uq_job_package_snapshot_job_id"
            ),
        ),
    )

    op.create_index(
        "ix_job_package_snapshot_job_id",
        "job_package_snapshot",
        ["job_id"],
        unique=False,
    )

    op.create_index(
        "ix_job_package_snapshot_package_id",
        "job_package_snapshot",
        ["package_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_job_package_snapshot_package_id",
        table_name="job_package_snapshot",
    )

    op.drop_index(
        "ix_job_package_snapshot_job_id",
        table_name="job_package_snapshot",
    )

    op.drop_table(
        "job_package_snapshot"
    )
