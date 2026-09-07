"""add step execution types for mixed projects

Revision ID: 9c5e7a1b3d42
Revises: 8b4d6f0a2c31
"""
from alembic import op
import sqlalchemy as sa

revision = "9c5e7a1b3d42"
down_revision = "8b4d6f0a2c31"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(sa.Column("execution_type", sa.String(length=20), nullable=True))
    op.execute(
        """
        UPDATE project_step
           SET execution_type = COALESCE(
               (SELECT project.execution_type FROM project WHERE project.id = project_step.project_id),
               'ansible'
           )
        """
    )
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.alter_column("execution_type", existing_type=sa.String(length=20), nullable=False, server_default="ansible")

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(sa.Column("execution_type", sa.String(length=20), nullable=True))
    op.execute(
        """
        UPDATE job_step
           SET execution_type = COALESCE(
               (SELECT job.execution_type FROM job WHERE job.id = job_step.job_id),
               'ansible'
           )
        """
    )
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.alter_column("execution_type", existing_type=sa.String(length=20), nullable=False, server_default="ansible")


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("execution_type")
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("execution_type")
