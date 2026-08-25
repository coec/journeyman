"""set historical runner foreign keys to null on runner deletion

Revision ID: d4f8b2a1c690
Revises: c1e4a7b9d230
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f8b2a1c690"
down_revision = "c1e4a7b9d230"
branch_labels = None
depends_on = None


_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _foreign_key_name(table_name, column_name):
    """Return the reflected FK name, synthesising one for unnamed SQLite FKs."""

    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("constrained_columns") == [column_name]:
            name = foreign_key.get("name")
            if name:
                return name
            referred_table = foreign_key.get("referred_table") or "runner"
            return "fk_{}_{}_{}".format(table_name, column_name, referred_table)
    raise RuntimeError(
        "Could not find foreign key for {}.{}".format(table_name, column_name)
    )


def _replace_runner_fk(table_name, column_name, ondelete):
    constraint_name = _foreign_key_name(table_name, column_name)
    with op.batch_alter_table(
        table_name,
        naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(constraint_name, type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_{}_{}_runner".format(table_name, column_name),
            "runner",
            [column_name],
            ["id"],
            ondelete=ondelete,
        )


def upgrade():
    _replace_runner_fk(
        "job_step_execution_slice",
        "required_runner_id",
        "SET NULL",
    )
    _replace_runner_fk(
        "job_step_execution_slice",
        "assigned_runner_id",
        "SET NULL",
    )
    _replace_runner_fk(
        "job_step_host_result",
        "runner_id",
        "SET NULL",
    )


def downgrade():
    _replace_runner_fk(
        "job_step_host_result",
        "runner_id",
        "RESTRICT",
    )
    _replace_runner_fk(
        "job_step_execution_slice",
        "assigned_runner_id",
        "RESTRICT",
    )
    _replace_runner_fk(
        "job_step_execution_slice",
        "required_runner_id",
        "RESTRICT",
    )
