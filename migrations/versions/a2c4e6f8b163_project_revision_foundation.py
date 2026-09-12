"""add Project revision and approval-state foundation

Revision ID: a2c4e6f8b163
Revises: f1b3c6e9a052
"""

from alembic import op
import sqlalchemy as sa


revision = "a2c4e6f8b163"
down_revision = "f1b3c6e9a052"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "approval_state",
                sa.String(length=32),
                nullable=False,
                server_default="development",
            )
        )

    op.create_table(
        "project_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "sequence",
            name="uq_project_revision_sequence",
        ),
    )
    op.create_index(
        op.f("ix_project_revision_project_id"),
        "project_revision",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_revision_digest"),
        "project_revision",
        ["digest"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_project_revision_digest"),
        table_name="project_revision",
    )
    op.drop_index(
        op.f("ix_project_revision_project_id"),
        table_name="project_revision",
    )
    op.drop_table("project_revision")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("approval_state")
