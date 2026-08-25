"""add execution environment runner system requirements

Revision ID: fc5d9e3a7b12
Revises: fb4c8d2e7a11
"""
from alembic import op
import sqlalchemy as sa


revision = "fc5d9e3a7b12"
down_revision = "fb4c8d2e7a11"
branch_labels = None
depends_on = None


def _lines(value):
    return [
        line.strip()
        for line in str(value or "").splitlines()
        if line.strip()
    ]


def upgrade():
    op.add_column(
        "environment",
        sa.Column(
            "system_requirements",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )

    # adcli was historically entered in Additional Python packages even though
    # it is an RPM package. Move that known package into the new runner-system
    # dependency field without attempting to guess other package types.
    bind = op.get_bind()
    environment = sa.table(
        "environment",
        sa.column("id", sa.Integer()),
        sa.column("pip_requirements", sa.Text()),
        sa.column("system_requirements", sa.Text()),
    )
    for row in bind.execute(
        sa.select(
            environment.c.id,
            environment.c.pip_requirements,
            environment.c.system_requirements,
        )
    ):
        pip_lines = _lines(row.pip_requirements)
        moved = [line for line in pip_lines if line.lower() == "adcli"]
        if not moved:
            continue
        kept = [line for line in pip_lines if line.lower() != "adcli"]
        system_lines = _lines(row.system_requirements)
        if not any(line.lower() == "adcli" for line in system_lines):
            system_lines.append("adcli")
        bind.execute(
            environment.update()
            .where(environment.c.id == row.id)
            .values(
                pip_requirements="\n".join(kept),
                system_requirements="\n".join(system_lines),
            )
        )


def downgrade():
    bind = op.get_bind()
    environment = sa.table(
        "environment",
        sa.column("id", sa.Integer()),
        sa.column("pip_requirements", sa.Text()),
        sa.column("system_requirements", sa.Text()),
    )
    for row in bind.execute(
        sa.select(
            environment.c.id,
            environment.c.pip_requirements,
            environment.c.system_requirements,
        )
    ):
        pip_lines = _lines(row.pip_requirements)
        for package in _lines(row.system_requirements):
            if package not in pip_lines:
                pip_lines.append(package)
        bind.execute(
            environment.update()
            .where(environment.c.id == row.id)
            .values(pip_requirements="\n".join(pip_lines))
        )

    op.drop_column("environment", "system_requirements")
