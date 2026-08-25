"""add audit log

Revision ID: 8a71c4d2e930
Revises: b18f4a6d2c90
Create Date: 2026-08-06 05:40:00
"""

from alembic import op
import sqlalchemy as sa

revision = "8a71c4d2e930"
down_revision = "b18f4a6d2c90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_username", sa.String(length=255), nullable=False),
        sa.Column("actor_object_guid", sa.String(length=36), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("authenticated_via", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=120), nullable=False),
        sa.Column("object_name", sa.String(length=255), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "occurred_at", "actor_username", "actor_object_guid", "action",
        "object_type", "result", "request_id",
    ):
        op.create_index(op.f("ix_audit_log_{}".format(column)), "audit_log", [column], unique=False)


def downgrade():
    op.drop_table("audit_log")
