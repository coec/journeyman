"""add project-step repository refresh option

Revision ID: 1c8d7f4a2b90
Revises: 7f4c2a9d1e63
"""

from alembic import op
import sqlalchemy as sa

revision = "1c8d7f4a2b90"
down_revision = "7f4c2a9d1e63"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(
            sa.Column("refresh_repository", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.alter_column("refresh_repository", server_default=None)


def downgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("refresh_repository")
