"""add user preferences
Revision ID: b6d9e3f2a518
Revises: a5c8e2f1d407
"""
from alembic import op
import sqlalchemy as sa
revision = "b6d9e3f2a518"
down_revision = "a5c8e2f1d407"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("user_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("hide_disabled_projects", sa.Boolean(), nullable=False, server_default=sa.false(),),
        sa.Column("hide_disabled_packages", sa.Boolean(), nullable=False, server_default=sa.false(),),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("username"))
    op.create_index(op.f("ix_user_preferences_username"), "user_preferences", ["username"], unique=True)
def downgrade():
    op.drop_index(op.f("ix_user_preferences_username"), table_name="user_preferences")
    op.drop_table("user_preferences")
