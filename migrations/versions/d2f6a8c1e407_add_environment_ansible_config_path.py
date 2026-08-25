"""add environment ansible config path

Revision ID: d2f6a8c1e407
Revises: a4d8f2c7b619
"""
from alembic import op
import sqlalchemy as sa

revision = "d2f6a8c1e407"
down_revision = "a4d8f2c7b619"
branch_labels = None
depends_on = None

DEFAULT_CONFIG = "/etc/ansible/ansible.cfg"


def upgrade():
    with op.batch_alter_table("environment") as batch_op:
        batch_op.add_column(sa.Column("ansible_config_path", sa.String(length=1000), nullable=False, server_default=DEFAULT_CONFIG))
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(sa.Column("ansible_config_path", sa.String(length=1000), nullable=False, server_default=DEFAULT_CONFIG))


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("ansible_config_path")
    with op.batch_alter_table("environment") as batch_op:
        batch_op.drop_column("ansible_config_path")
