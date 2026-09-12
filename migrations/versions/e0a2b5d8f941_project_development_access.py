"""add Project development/test access

Revision ID: e0a2b5d8f941
Revises: d9f1a4c7e830
"""

from alembic import op
import sqlalchemy as sa

revision = "e0a2b5d8f941"
down_revision = "d9f1a4c7e830"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_development_user",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_account_id",
            sa.Integer(),
            sa.ForeignKey("user_account.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "project_development_team",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("team.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade():
    op.drop_table("project_development_team")
    op.drop_table("project_development_user")
