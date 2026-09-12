"""add Project management approval workflow

Revision ID: c4e6a8b0d385
Revises: b3d5f7a9c274
"""

from alembic import op
import sqlalchemy as sa


revision = "c4e6a8b0d385"
down_revision = "b3d5f7a9c274"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_management_approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("technical_review_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("approver_username", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["technical_review_id"],
            ["project_review.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id",
        "revision_id",
        "technical_review_id",
        "requested_by",
        "approver_username",
        "status",
    ):
        op.create_index(
            op.f("ix_project_management_approval_{}".format(column)),
            "project_management_approval",
            [column],
            unique=False,
        )


def downgrade():
    for column in (
        "status",
        "approver_username",
        "requested_by",
        "technical_review_id",
        "revision_id",
        "project_id",
    ):
        op.drop_index(
            op.f("ix_project_management_approval_{}".format(column)),
            table_name="project_management_approval",
        )
    op.drop_table("project_management_approval")
