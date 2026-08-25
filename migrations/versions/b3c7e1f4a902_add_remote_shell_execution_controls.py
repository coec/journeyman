"""add remote shell execution controls

Revision ID: b3c7e1f4a902
Revises: f8c3a1d6e724
"""

from alembic import op
import sqlalchemy as sa


revision = "b3c7e1f4a902"
down_revision = "f8c3a1d6e724"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "remote_shell_become",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "remote_shell_serial",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(
            sa.Column(
                "remote_shell_become",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "remote_shell_serial",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("remote_shell_serial")
        batch_op.drop_column("remote_shell_become")

    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("remote_shell_serial")
        batch_op.drop_column("remote_shell_become")
