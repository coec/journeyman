"""add per-step oversight boundaries

Revision ID: f0b1c2d3e4a5
Revises: e1a6c9f4b270
"""

import json

from alembic import op
import sqlalchemy as sa

revision = "f0b1c2d3e4a5"
down_revision = "e1a6c9f4b270"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_step") as batch:
        batch.add_column(
            sa.Column(
                "oversight_after",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("job_step") as batch:
        batch.add_column(
            sa.Column(
                "oversight_required_before",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT ps.id, ps.project_id, ps.position, ps.depends_on_json,
                   p.oversight_required_between_all_steps
              FROM project_step ps
              JOIN project p ON p.id = ps.project_id
            """
        )
    ).mappings().all()

    dependent_positions = {}
    for row in rows:
        try:
            dependencies = json.loads(row["depends_on_json"] or "[]")
        except (TypeError, ValueError):
            dependencies = []
        bucket = dependent_positions.setdefault(row["project_id"], set())
        for dependency in dependencies:
            try:
                bucket.add(int(dependency))
            except (TypeError, ValueError):
                continue

    for row in rows:
        if (
            row["oversight_required_between_all_steps"]
            and row["position"] in dependent_positions.get(row["project_id"], set())
        ):
            connection.execute(
                sa.text(
                    "UPDATE project_step SET oversight_after = :enabled WHERE id = :id"
                ),
                {"enabled": True, "id": row["id"]},
            )


def downgrade():
    with op.batch_alter_table("job_step") as batch:
        batch.drop_column("oversight_required_before")
    with op.batch_alter_table("project_step") as batch:
        batch.drop_column("oversight_after")
