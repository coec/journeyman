"""preserve Reaction identity when deleting Reactors

Revision ID: ff8c4e2a7d35
Revises: fe7b3d1a9c24
"""

from alembic import op
import sqlalchemy as sa

revision = "ff8c4e2a7d35"
down_revision = "fe7b3d1a9c24"
branch_labels = None
depends_on = None

_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _reaction_reactor_fk_name(bind):
    for foreign_key in sa.inspect(bind).get_foreign_keys("reaction"):
        if foreign_key.get("constrained_columns") == ["reactor_id"]:
            return foreign_key.get("name") or "fk_reaction_reactor_id_reactor"
    raise RuntimeError("Unable to find reaction.reactor_id foreign key.")


def upgrade():
    with op.batch_alter_table("reaction") as batch:
        batch.add_column(
            sa.Column("reactor_name_snapshot", sa.String(length=120), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("source_name_snapshot", sa.String(length=120), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("package_name_snapshot", sa.String(length=120), nullable=False, server_default="")
        )

    # Backfill immutable display identity before reactor_id is allowed to be
    # cleared.  Correlated subqueries are supported by both SQLite and
    # PostgreSQL and avoid relying on backend-specific UPDATE ... FROM syntax.
    op.execute(
        sa.text(
            "UPDATE reaction SET reactor_name_snapshot = COALESCE("
            "(SELECT reactor.name FROM reactor WHERE reactor.id = reaction.reactor_id), '')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reaction SET source_name_snapshot = COALESCE("
            "(SELECT signal_source.name FROM signal "
            "JOIN signal_source ON signal_source.id = signal.source_id "
            "WHERE signal.id = reaction.signal_id), '')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE reaction SET package_name_snapshot = COALESCE("
            "(SELECT project_package.name FROM project_package "
            "WHERE project_package.id = reaction.package_id), '')"
        )
    )

    bind = op.get_bind()
    foreign_key_name = _reaction_reactor_fk_name(bind)
    with op.batch_alter_table(
        "reaction",
        naming_convention=_NAMING_CONVENTION,
    ) as batch:
        batch.drop_constraint(foreign_key_name, type_="foreignkey")
        batch.alter_column(
            "reactor_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.create_foreign_key(
            "fk_reaction_reactor_id_reactor",
            "reactor",
            ["reactor_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    bind = op.get_bind()
    orphaned = bind.execute(
        sa.text("SELECT COUNT(*) FROM reaction WHERE reactor_id IS NULL")
    ).scalar_one()
    if orphaned:
        raise RuntimeError(
            "Cannot downgrade while Reaction history exists for deleted Reactors."
        )

    foreign_key_name = _reaction_reactor_fk_name(bind)
    with op.batch_alter_table(
        "reaction",
        naming_convention=_NAMING_CONVENTION,
    ) as batch:
        batch.drop_constraint(foreign_key_name, type_="foreignkey")
        batch.alter_column(
            "reactor_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch.create_foreign_key(
            "fk_reaction_reactor_id_reactor",
            "reactor",
            ["reactor_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_column("package_name_snapshot")
        batch.drop_column("source_name_snapshot")
        batch.drop_column("reactor_name_snapshot")
