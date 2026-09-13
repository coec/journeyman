"""correct Package schedule answer storage type

Revision ID: e5a9d3b7c124
Revises: d4f8c2a6b013
"""

from alembic import op
import sqlalchemy as sa


revision = "e5a9d3b7c124"
down_revision = "d4f8c2a6b013"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.alter_column(
            "project_schedule",
            "encrypted_package_answers",
            existing_type=sa.Text(),
            type_=sa.LargeBinary(),
            existing_nullable=True,
            postgresql_using=(
                "convert_to(encrypted_package_answers, 'UTF8')"
            ),
        )

        op.alter_column(
            "project_schedule",
            "package_answers_key_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=120),
            existing_nullable=True,
        )

    else:
        with op.batch_alter_table("project_schedule") as batch_op:
            batch_op.alter_column(
                "encrypted_package_answers",
                existing_type=sa.Text(),
                type_=sa.LargeBinary(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                "package_answers_key_id",
                existing_type=sa.String(length=64),
                type_=sa.String(length=120),
                existing_nullable=True,
            )


def downgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.alter_column(
            "project_schedule",
            "encrypted_package_answers",
            existing_type=sa.LargeBinary(),
            type_=sa.Text(),
            existing_nullable=True,
            postgresql_using=(
                "convert_from(encrypted_package_answers, 'UTF8')"
            ),
        )

        op.alter_column(
            "project_schedule",
            "package_answers_key_id",
            existing_type=sa.String(length=120),
            type_=sa.String(length=64),
            existing_nullable=True,
        )

    else:
        with op.batch_alter_table("project_schedule") as batch_op:
            batch_op.alter_column(
                "encrypted_package_answers",
                existing_type=sa.LargeBinary(),
                type_=sa.Text(),
                existing_nullable=True,
            )
            batch_op.alter_column(
                "package_answers_key_id",
                existing_type=sa.String(length=120),
                type_=sa.String(length=64),
                existing_nullable=True,
            )
