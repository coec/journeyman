"""add runner runtime dependency integrity and audit state

Revision ID: 1b0e6a4c9d57
Revises: 0a9d5f3b8c46
"""

from alembic import op
import sqlalchemy as sa

revision = "1b0e6a4c9d57"
down_revision = "0a9d5f3b8c46"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch:
        batch.add_column(sa.Column("runtime_dependencies_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("runtime_dependencies_reported_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("runtime_dependency_audit_status", sa.String(length=32), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("runtime_dependency_audit_message", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("runtime_dependency_audit_checked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("runtime_dependency_audit_fingerprint", sa.String(length=64), nullable=False, server_default=""))
        batch.add_column(sa.Column("runtime_dependency_audit_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("runner") as batch:
        batch.drop_column("runtime_dependency_audit_json")
        batch.drop_column("runtime_dependency_audit_fingerprint")
        batch.drop_column("runtime_dependency_audit_checked_at")
        batch.drop_column("runtime_dependency_audit_message")
        batch.drop_column("runtime_dependency_audit_status")
        batch.drop_column("runtime_dependencies_reported_at")
        batch.drop_column("runtime_dependencies_json")
