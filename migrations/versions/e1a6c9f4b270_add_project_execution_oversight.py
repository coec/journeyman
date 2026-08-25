"""add project execution oversight

Revision ID: e1a6c9f4b270
Revises: d3a7f1c9e540
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a6c9f4b270"
down_revision = "d3a7f1c9e540"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column(
            "oversight_required_between_all_steps",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ))

    with op.batch_alter_table("job") as batch:
        batch.add_column(sa.Column(
            "oversight_required_between_all_steps",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ))
        batch.add_column(sa.Column(
            "oversight_reviewer",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ))

    with op.batch_alter_table("job_step") as batch:
        batch.add_column(sa.Column(
            "oversight_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ))

    with op.batch_alter_table("job_repository_snapshot") as batch:
        batch.add_column(sa.Column(
            "repository_commit_message",
            sa.String(length=1000),
            nullable=False,
            server_default="",
        ))
        batch.add_column(sa.Column(
            "repository_commit_author",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ))
        batch.add_column(sa.Column(
            "repository_commit_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ))


def downgrade():
    with op.batch_alter_table("job_repository_snapshot") as batch:
        batch.drop_column("repository_commit_at")
        batch.drop_column("repository_commit_author")
        batch.drop_column("repository_commit_message")

    with op.batch_alter_table("job_step") as batch:
        batch.drop_column("oversight_approved")

    with op.batch_alter_table("job") as batch:
        batch.drop_column("oversight_reviewer")
        batch.drop_column("oversight_required_between_all_steps")

    with op.batch_alter_table("project") as batch:
        batch.drop_column("oversight_required_between_all_steps")
