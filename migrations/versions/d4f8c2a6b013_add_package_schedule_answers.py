"""add encrypted Package schedule answers

Revision ID: d4f8c2a6b013
Revises: d3e7b1c5a902
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f8c2a6b013"
down_revision = "d3e7b1c5a902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_schedule") as batch_op:
        batch_op.add_column(
            sa.Column(
                "encrypted_package_answers",
                sa.Text(),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "package_answers_key_id",
                sa.String(length=64),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("project_schedule") as batch_op:
        batch_op.drop_column("package_answers_key_id")
        batch_op.drop_column("encrypted_package_answers")
