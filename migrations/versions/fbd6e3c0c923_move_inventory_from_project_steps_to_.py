"""move inventory from project steps to project

Revision ID: fbd6e3c0c923
Revises: 9eeaf21b5a11
Create Date: 2026-07-31 22:10:07.736766

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fbd6e3c0c923'
down_revision = '9eeaf21b5a11'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table(
        "project",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "inventory_id",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.create_index(
            "ix_project_inventory_id",
            ["inventory_id"],
            unique=False,
        )

        batch_op.create_foreign_key(
            "fk_project_inventory_id_inventory_source",
            "inventory_source",
            ["inventory_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    connection = op.get_bind()

    project_rows = connection.execute(
        sa.text(
            """
            SELECT
                p.id AS project_id,
                ps.inventory_source_id AS inventory_id
            FROM project AS p
            LEFT JOIN project_step AS ps
                ON ps.project_id = p.id
            ORDER BY
                p.id,
                ps.position
            """
        )
    ).mappings().all()

    inventories_by_project = {}

    for row in project_rows:
        project_id = row["project_id"]
        inventory_id = row["inventory_id"]

        inventories_by_project.setdefault(
            project_id,
            set(),
        )

        if inventory_id is not None:
            inventories_by_project[project_id].add(
                inventory_id
            )

    conflicting_projects = {
        project_id: inventory_ids
        for project_id, inventory_ids
        in inventories_by_project.items()
        if len(inventory_ids) > 1
    }

    if conflicting_projects:
        details = ", ".join(
            (
                f"project {project_id}: "
                f"{sorted(inventory_ids)}"
            )
            for project_id, inventory_ids
            in sorted(conflicting_projects.items())
        )

        raise RuntimeError(
            "Cannot migrate projects whose steps use different "
            f"inventories: {details}"
        )

    for project_id, inventory_ids in (
        inventories_by_project.items()
    ):
        inventory_id = (
            next(iter(inventory_ids))
            if inventory_ids
            else None
        )

        connection.execute(
            sa.text(
                """
                UPDATE project
                SET inventory_id = :inventory_id
                WHERE id = :project_id
                """
            ),
            {
                "inventory_id": inventory_id,
                "project_id": project_id,
            },
        )

    with op.batch_alter_table(
        "project_step",
        schema=None,
    ) as batch_op:
        batch_op.drop_column(
            "inventory_source_id"
        )


def downgrade():
    with op.batch_alter_table(
        "project_step",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "inventory_source_id",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.create_foreign_key(
            "fk_project_step_inventory_source_id",
            "inventory_source",
            ["inventory_source_id"],
            ["id"],
        )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            UPDATE project_step
            SET inventory_source_id = (
                SELECT project.inventory_id
                FROM project
                WHERE project.id = project_step.project_id
            )
            """
        )
    )

    with op.batch_alter_table(
        "project",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_project_inventory_id_inventory_source",
            type_="foreignkey",
        )

        batch_op.drop_index(
            "ix_project_inventory_id"
        )

        batch_op.drop_column(
            "inventory_id"
        )
