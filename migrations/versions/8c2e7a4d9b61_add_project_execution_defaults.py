"""add project execution defaults

Revision ID: 8c2e7a4d9b61
Revises: 74b1c9e2d5f0
"""

from alembic import op
import sqlalchemy as sa

revision = "8c2e7a4d9b61"
down_revision = "74b1c9e2d5f0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("repository_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("environment_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_project_repository_id_repository",
            "repository",
            ["repository_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_project_environment_id_environment",
            "environment",
            ["environment_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_project_repository_id", ["repository_id"])
        batch_op.create_index("ix_project_environment_id", ["environment_id"])

    op.create_table(
        "project_credential",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["credential.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "credential_id"),
    )

    with op.batch_alter_table("project_step") as batch_op:
        batch_op.alter_column("repository_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(
            sa.Column(
                "credentials_override",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Existing Projects already define every value at step level. Mark their
    # credential selections as explicit overrides so upgrades preserve behaviour.
    op.execute(
        "UPDATE project_step SET credentials_override = TRUE "
        "WHERE id IN (SELECT DISTINCT project_step_id FROM project_step_credential)"
    )


def downgrade():
    with op.batch_alter_table("project_step") as batch_op:
        batch_op.drop_column("credentials_override")
        batch_op.alter_column("repository_id", existing_type=sa.Integer(), nullable=False)

    op.drop_table("project_credential")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_index("ix_project_environment_id")
        batch_op.drop_index("ix_project_repository_id")
        batch_op.drop_constraint("fk_project_environment_id_environment", type_="foreignkey")
        batch_op.drop_constraint("fk_project_repository_id_repository", type_="foreignkey")
        batch_op.drop_column("environment_id")
        batch_op.drop_column("repository_id")
