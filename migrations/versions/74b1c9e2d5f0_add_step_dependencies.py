"""add workflow step dependencies

Revision ID: 74b1c9e2d5f0
Revises: 6a3f8d2c4e71
"""

from alembic import op
import sqlalchemy as sa
import json

revision = "74b1c9e2d5f0"
down_revision = "6a3f8d2c4e71"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.add_column(
            sa.Column("depends_on_json", sa.Text(), nullable=False, server_default="[]")
        )

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(
            sa.Column("depends_on_json", sa.Text(), nullable=False, server_default="[]")
        )

    connection = op.get_bind()
    projects = connection.execute(
        sa.text("SELECT DISTINCT project_id FROM project_step")
    ).fetchall()
    for (project_id,) in projects:
        rows = connection.execute(
            sa.text(
                "SELECT id, position FROM project_step "
                "WHERE project_id = :project_id ORDER BY position"
            ),
            {"project_id": project_id},
        ).fetchall()
        previous_position = None
        for step_id, position in rows:
            dependencies = [] if previous_position is None else [previous_position]
            connection.execute(
                sa.text(
                    "UPDATE project_step SET depends_on_json = :dependencies "
                    "WHERE id = :step_id"
                ),
                {"dependencies": json.dumps(dependencies), "step_id": step_id},
            )
            previous_position = position

    jobs = connection.execute(
        sa.text("SELECT DISTINCT job_id FROM job_step")
    ).fetchall()
    for (job_id,) in jobs:
        rows = connection.execute(
            sa.text(
                "SELECT id, position FROM job_step "
                "WHERE job_id = :job_id ORDER BY position"
            ),
            {"job_id": job_id},
        ).fetchall()
        previous_position = None
        for step_id, position in rows:
            dependencies = [] if previous_position is None else [previous_position]
            connection.execute(
                sa.text(
                    "UPDATE job_step SET depends_on_json = :dependencies "
                    "WHERE id = :step_id"
                ),
                {"dependencies": json.dumps(dependencies), "step_id": step_id},
            )
            previous_position = position


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_column("depends_on_json")
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("depends_on_json")
