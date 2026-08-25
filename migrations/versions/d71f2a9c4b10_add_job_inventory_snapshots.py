"""add job inventory snapshots

Revision ID: d71f2a9c4b10
Revises: 9461fb238507
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa


revision = "d71f2a9c4b10"
down_revision = "9461fb238507"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_inventory_snapshot",
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
            "inventory_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "inventory_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "inventory_type",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "host_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "content_path",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["inventory_id"],
            ["inventory_source.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "version",
            name="uq_job_inventory_snapshot_version",
        ),
    )

    op.create_index(
        "ix_job_inventory_snapshot_job_id",
        "job_inventory_snapshot",
        ["job_id"],
        unique=False,
    )

    op.create_index(
        "ix_job_inventory_snapshot_inventory_id",
        "job_inventory_snapshot",
        ["inventory_id"],
        unique=False,
    )

    with op.batch_alter_table(
        "job_step",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "job_inventory_snapshot_id",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.create_index(
            "ix_job_step_job_inventory_snapshot_id",
            ["job_inventory_snapshot_id"],
            unique=False,
        )

        batch_op.create_foreign_key(
            "fk_job_step_job_inventory_snapshot",
            "job_inventory_snapshot",
            ["job_inventory_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table(
        "job_step",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_job_step_job_inventory_snapshot",
            type_="foreignkey",
        )

        batch_op.drop_index(
            "ix_job_step_job_inventory_snapshot_id"
        )

        batch_op.drop_column(
            "job_inventory_snapshot_id"
        )

    op.drop_index(
        "ix_job_inventory_snapshot_inventory_id",
        table_name="job_inventory_snapshot",
    )

    op.drop_index(
        "ix_job_inventory_snapshot_job_id",
        table_name="job_inventory_snapshot",
    )

    op.drop_table(
        "job_inventory_snapshot"
    )
