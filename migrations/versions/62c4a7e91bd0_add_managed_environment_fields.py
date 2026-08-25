"""add managed environment fields

Revision ID: 62c4a7e91bd0
Revises: 31d9f0c4a7b2
"""
from alembic import op
import sqlalchemy as sa

revision = "62c4a7e91bd0"
down_revision = "31d9f0c4a7b2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("environment") as batch_op:
        batch_op.add_column(sa.Column("is_managed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("python_interpreter", sa.String(length=1000), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ansible_spec", sa.String(length=255), nullable=False, server_default="ansible-core"))
        batch_op.add_column(sa.Column("pip_requirements", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("collection_requirements", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("build_status", sa.String(length=32), nullable=False, server_default="not_built"))
        batch_op.add_column(sa.Column("build_message", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("environment") as batch_op:
        batch_op.drop_column("build_message")
        batch_op.drop_column("build_status")
        batch_op.drop_column("collection_requirements")
        batch_op.drop_column("pip_requirements")
        batch_op.drop_column("ansible_spec")
        batch_op.drop_column("python_interpreter")
        batch_op.drop_column("is_managed")
