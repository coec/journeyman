"""expand local authorization roles

Revision ID: b7d4e9a2c610
Revises: a6d8f1b3c520
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d4e9a2c610"
down_revision = "a6d8f1b3c520"
branch_labels = None
depends_on = None


ROLE_DESCRIPTIONS = {
    "Admin": "Manage Journeyman itself, including users, teams, and system configuration.",
    "Automation Admin": "Manage Projects, Packages, Signals, Sources, and Reactors.",
    "Resource Admin": "Manage repositories, credentials, inventories, environments, and other execution resources.",
    "User": "Launch Packages assigned directly or inherited through team membership.",
    "Auditor": "Read-only access to Journeyman configuration, settings, jobs, approvals, and audit information.",
}

OLD_ROLE_DESCRIPTIONS = {
    "Admin": "Administer Journeyman configuration and authorization.",
    "User": "Use Journeyman capabilities explicitly granted to the account.",
    "Auditor": "Read-only audit and governance access.",
}


def upgrade():
    bind = op.get_bind()
    role = sa.table(
        "authorization_role",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("builtin", sa.Boolean),
    )

    existing = {
        row[0]
        for row in bind.execute(sa.select(role.c.name)).fetchall()
    }

    for name, description in ROLE_DESCRIPTIONS.items():
        if name in existing:
            bind.execute(
                role.update()
                .where(role.c.name == name)
                .values(description=description, builtin=True)
            )
        else:
            bind.execute(
                role.insert().values(
                    name=name,
                    description=description,
                    builtin=True,
                )
            )


def downgrade():
    bind = op.get_bind()
    role = sa.table(
        "authorization_role",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )

    bind.execute(
        role.delete().where(
            role.c.name.in_(["Automation Admin", "Resource Admin"])
        )
    )

    for name, description in OLD_ROLE_DESCRIPTIONS.items():
        bind.execute(
            role.update()
            .where(role.c.name == name)
            .values(description=description)
        )
