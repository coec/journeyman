"""add notification targets, rules, events and deliveries

Revision ID: a1c4e7f9b203
Revises: f0b1c2d3e4a5
"""

from alembic import op
import sqlalchemy as sa

revision = "a1c4e7f9b203"
down_revision = "f0b1c2d3e4a5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_target",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("host", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tls_mode", sa.String(length=16), nullable=False, server_default="starttls"),
        sa.Column("username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sender", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("recipients", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("syslog_protocol", sa.String(length=8), nullable=False, server_default="udp"),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=True),
        sa.Column("secret_key_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_target_channel", "notification_target", ["channel"])

    op.create_table(
        "notification_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("notification_target.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "event_type", "target_id", name="uq_notification_rule_scope_event_target"),
    )
    op.create_index("ix_notification_rule_scope_type", "notification_rule", ["scope_type"])
    op.create_index("ix_notification_rule_scope_id", "notification_rule", ["scope_id"])
    op.create_index("ix_notification_rule_event_type", "notification_rule", ["event_type"])
    op.create_index("ix_notification_rule_target_id", "notification_rule", ["target_id"])

    op.create_table(
        "notification_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id", ondelete="CASCADE"), nullable=True),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey("job_step.id", ondelete="CASCADE"), nullable=True),
        sa.Column("reaction_id", sa.Integer(), sa.ForeignKey("reaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_type", "job_id", "step_id", "event_key", name="uq_notification_event_identity"),
    )
    for name in ("event_type", "job_id", "step_id", "reaction_id", "created_at", "processed_at"):
        op.create_index("ix_notification_event_{}".format(name), "notification_event", [name])

    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("notification_event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("notification_target.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", "target_id", name="uq_notification_delivery_event_target"),
    )
    for name in ("event_id", "target_id", "status"):
        op.create_index("ix_notification_delivery_{}".format(name), "notification_delivery", [name])


def downgrade():
    op.drop_table("notification_delivery")
    op.drop_table("notification_event")
    op.drop_table("notification_rule")
    op.drop_table("notification_target")
