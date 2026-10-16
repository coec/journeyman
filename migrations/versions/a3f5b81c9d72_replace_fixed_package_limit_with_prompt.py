"""replace fixed Package limit with prompted Package limit input

Revision ID: a3f5b81c9d72
Revises: 9f2c7e4b6d11
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f5b81c9d72"
down_revision = "9f2c7e4b6d11"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve data instead of silently dropping already configured limits.
    conn = op.get_bind()
    existing = conn.execute(sa.text(
        "SELECT name FROM project_package WHERE COALESCE(\"limit\", '') <> '' LIMIT 1"
    )).scalar()
    if existing is not None:
        raise RuntimeError(
            "Clear the fixed host limit on Package {!r} before upgrading; "
            "fixed Package limits have been replaced by a dispatch prompt."
            .format(existing)
        )

    with op.batch_alter_table("project_package") as batch:
        batch.drop_column("limit")


def downgrade():
    with op.batch_alter_table("project_package") as batch:
        batch.add_column(sa.Column(
            "limit", sa.String(length=500), nullable=False,
            server_default="",
        ))
