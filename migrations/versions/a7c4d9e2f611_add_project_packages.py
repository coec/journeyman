"""add project packages

Revision ID: a7c4d9e2f611
Revises: e2453c8a7b91
Create Date: 2026-08-05

"""
from alembic import op
import sqlalchemy as sa


revision = "a7c4d9e2f611"
down_revision = "e2453c8a7b91"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_package",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "owner",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "access_mode",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "warning_message",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "confirmation_required",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "confirmation_message",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "fixed_vars_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "name",
            name="uq_project_package_name",
        ),
    )

    op.create_index(
        "ix_project_package_project_id",
        "project_package",
        ["project_id"],
        unique=False,
    )

    op.create_index(
        "ix_project_package_owner",
        "project_package",
        ["owner"],
        unique=False,
    )

    op.create_table(
        "project_package_input",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "package_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "variable_name",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "label",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "help_text",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "input_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "required",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "is_secret",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "default_value_json",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "choices_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "validation_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "conditions_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "display_role",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "binding_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["project_package.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id",
            "position",
            name=(
                "uq_project_package_input_"
                "package_position"
            ),
        ),
        sa.UniqueConstraint(
            "package_id",
            "variable_name",
            name=(
                "uq_project_package_input_"
                "package_variable"
            ),
        ),
    )

    op.create_index(
        "ix_project_package_input_package_id",
        "project_package_input",
        ["package_id"],
        unique=False,
    )

    op.create_table(
        "project_package_permission",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "package_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "principal_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "principal_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["project_package.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id",
            "principal_type",
            "principal_name",
            name=(
                "uq_project_package_permission_"
                "package_principal"
            ),
        ),
    )

    op.create_index(
        "ix_project_package_permission_package_id",
        "project_package_permission",
        ["package_id"],
        unique=False,
    )

    op.create_index(
        "ix_project_package_permission_principal",
        "project_package_permission",
        [
            "principal_type",
            "principal_name",
        ],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_project_package_permission_principal",
        table_name="project_package_permission",
    )

    op.drop_index(
        "ix_project_package_permission_package_id",
        table_name="project_package_permission",
    )

    op.drop_table(
        "project_package_permission"
    )

    op.drop_index(
        "ix_project_package_input_package_id",
        table_name="project_package_input",
    )

    op.drop_table(
        "project_package_input"
    )

    op.drop_index(
        "ix_project_package_owner",
        table_name="project_package",
    )

    op.drop_index(
        "ix_project_package_project_id",
        table_name="project_package",
    )

    op.drop_table(
        "project_package"
    )
