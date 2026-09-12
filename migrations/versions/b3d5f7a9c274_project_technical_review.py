"""add Project technical review workflow

Revision ID: b3d5f7a9c274
Revises: a2c4e6f8b163
"""

from alembic import op
import sqlalchemy as sa


revision = "b3d5f7a9c274"
down_revision = "a2c4e6f8b163"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column(
            "reviewer_username",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "decision_comment",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["project_revision.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_review_project_id"),
        "project_review",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_review_revision_id"),
        "project_review",
        ["revision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_review_requested_by"),
        "project_review",
        ["requested_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_review_reviewer_username"),
        "project_review",
        ["reviewer_username"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_review_status"),
        "project_review",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_project_review_status"),
        table_name="project_review",
    )
    op.drop_index(
        op.f("ix_project_review_reviewer_username"),
        table_name="project_review",
    )
    op.drop_index(
        op.f("ix_project_review_requested_by"),
        table_name="project_review",
    )
    op.drop_index(
        op.f("ix_project_review_revision_id"),
        table_name="project_review",
    )
    op.drop_index(
        op.f("ix_project_review_project_id"),
        table_name="project_review",
    )
    op.drop_table("project_review")
