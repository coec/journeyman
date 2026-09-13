"""add runner PKI certificate identity

Revision ID: c8e4f1a7b930
Revises: f7b9d1e3a618
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e4f1a7b930"
down_revision = "f7b9d1e3a618"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.add_column(
            sa.Column("pki_certificate_serial", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "pki_certificate_fingerprint_sha256",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column("pki_certificate_not_before_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pki_certificate_not_after_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pki_certificate_issued_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_runner_pki_certificate_fingerprint_sha256",
            ["pki_certificate_fingerprint_sha256"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("runner") as batch_op:
        batch_op.drop_index("ix_runner_pki_certificate_fingerprint_sha256")
        batch_op.drop_column("pki_certificate_issued_at")
        batch_op.drop_column("pki_certificate_not_after_at")
        batch_op.drop_column("pki_certificate_not_before_at")
        batch_op.drop_column("pki_certificate_fingerprint_sha256")
        batch_op.drop_column("pki_certificate_serial")
