"""add reaction automation

Revision ID: d4a8c1e7f930
Revises: b6d9e3f2a518
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a8c1e7f930"
down_revision = "b6d9e3f2a518"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_package") as batch:
        batch.add_column(sa.Column("allow_as_reaction", sa.Boolean(), server_default=sa.false(), nullable=False))

    op.create_table(
        "signal_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_uuid", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False),
        sa.Column("allowed_networks_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("zabbix_url", sa.String(1000), server_default="", nullable=False),
        sa.Column("encrypted_hmac_secret", sa.LargeBinary(), nullable=True),
        sa.Column("hmac_secret_key_id", sa.String(120), nullable=True),
        sa.Column("secret_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_id", sa.Integer(), sa.ForeignKey("runner.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sender_ip", sa.String(64), server_default="", nullable=False),
        sa.Column("accepted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_uuid"),
        sa.UniqueConstraint("name", name="uq_signal_source_name"),
    )
    op.create_index("ix_signal_source_source_uuid", "signal_source", ["source_uuid"], unique=True)
    op.create_index("ix_signal_source_runner_id", "signal_source", ["runner_id"])

    op.create_table(
        "signal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("signal_source.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_signal_id", sa.String(255), nullable=False),
        sa.Column("signal_type", sa.String(120), server_default="", nullable=False),
        sa.Column("signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("host", sa.String(255), server_default="", nullable=False),
        sa.Column("severity", sa.String(64), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("fields_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("raw_payload", sa.Text(), server_default="", nullable=False),
        sa.Column("sender_ip", sa.String(64), server_default="", nullable=False),
        sa.Column("runner_id", sa.Integer(), sa.ForeignKey("runner.id", ondelete="SET NULL"), nullable=True),
        sa.Column("processing_status", sa.String(32), server_default="accepted", nullable=False),
        sa.UniqueConstraint("source_id", "external_signal_id", name="uq_signal_source_external_id"),
    )
    op.create_index("ix_signal_source_id", "signal", ["source_id"])
    op.create_index("ix_signal_received_at", "signal", ["received_at"])
    op.create_index("ix_signal_host", "signal", ["host"])
    op.create_index("ix_signal_runner_id", "signal", ["runner_id"])

    op.create_table(
        "reactor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("mode", sa.String(32), server_default="observe", nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("signal_source.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("project_package.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("match_json", sa.Text(), server_default='{"all":[]}', nullable=False),
        sa.Column("mappings_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_concurrency", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_reactor_name"),
    )
    op.create_index("ix_reactor_source_id", "reactor", ["source_id"])
    op.create_index("ix_reactor_package_id", "reactor", ["package_id"])

    op.create_table(
        "reaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signal.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reactor_id", sa.Integer(), sa.ForeignKey("reactor.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("project_package.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resolved_inputs_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("message", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("signal_id", "reactor_id", name="uq_reaction_signal_reactor"),
    )
    for name, cols in (
        ("ix_reaction_signal_id", ["signal_id"]),
        ("ix_reaction_reactor_id", ["reactor_id"]),
        ("ix_reaction_package_id", ["package_id"]),
        ("ix_reaction_job_id", ["job_id"]),
        ("ix_reaction_created_at", ["created_at"]),
    ):
        op.create_index(name, "reaction", cols)


def downgrade():
    op.drop_table("reaction")
    op.drop_table("reactor")
    op.drop_table("signal")
    op.drop_table("signal_source")
    with op.batch_alter_table("project_package") as batch:
        batch.drop_column("allow_as_reaction")
