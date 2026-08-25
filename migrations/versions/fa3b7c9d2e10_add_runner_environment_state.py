"""add runner execution environment state

Revision ID: fa3b7c9d2e10
Revises: e9a4c2f7b610
"""
from alembic import op
import sqlalchemy as sa

revision = "fa3b7c9d2e10"
down_revision = "e9a4c2f7b610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runner_environment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="not_installed",
        ),
        sa.Column(
            "environment_revision",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "local_path",
            sa.String(length=1000),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("last_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name="fk_runner_environment_environment_id_environment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runner.id"],
            name="fk_runner_environment_runner_id_runner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runner_id",
            "environment_id",
            name="uq_runner_environment_runner_environment",
        ),
    )
    op.create_index(
        "ix_runner_environment_runner_id",
        "runner_environment",
        ["runner_id"],
        unique=False,
    )
    op.create_index(
        "ix_runner_environment_environment_id",
        "runner_environment",
        ["environment_id"],
        unique=False,
    )

    with op.batch_alter_table("job_step") as batch_op:
        batch_op.add_column(sa.Column("environment_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "environment_revision",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            "ix_job_step_environment_id",
            ["environment_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_job_step_environment_id_environment",
            "environment",
            ["environment_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Preserve the live Environment identity for existing historical JobStep
    # rows where the Environment still exists.  Historical rows intentionally
    # keep an empty revision so the new runner eligibility rule is not applied
    # retrospectively to already-queued work.
    bind = op.get_bind()
    environments = bind.execute(sa.text("SELECT id, name FROM environment")).fetchall()
    for environment_id, environment_name in environments:
        bind.execute(
            sa.text(
                "UPDATE job_step SET environment_id = :environment_id "
                "WHERE environment_name = :environment_name"
            ),
            {
                "environment_id": environment_id,
                "environment_name": environment_name,
            },
        )


def downgrade():
    with op.batch_alter_table("job_step") as batch_op:
        batch_op.drop_constraint(
            "fk_job_step_environment_id_environment",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_job_step_environment_id")
        batch_op.drop_column("environment_revision")
        batch_op.drop_column("environment_id")

    op.drop_index(
        "ix_runner_environment_environment_id",
        table_name="runner_environment",
    )
    op.drop_index(
        "ix_runner_environment_runner_id",
        table_name="runner_environment",
    )
    op.drop_table("runner_environment")
