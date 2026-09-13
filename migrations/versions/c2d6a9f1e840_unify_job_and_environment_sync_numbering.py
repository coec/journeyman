"""unify Job and Environment sync numbering

Revision ID: c2d6a9f1e840
Revises: fbf4d7e0c263
"""

from alembic import op
import sqlalchemy as sa

revision = "c2d6a9f1e840"
down_revision = "fbf4d7e0c263"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_work_item_kind", "work_item", ["kind"], unique=False)

    op.add_column(
        "runner_environment_sync",
        sa.Column("work_item_id", sa.Integer(), nullable=True),
    )

    bind = op.get_bind()
    metadata = sa.MetaData()
    work_item = sa.Table(
        "work_item",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
    )
    job = sa.Table(
        "job",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    sync = sa.Table(
        "runner_environment_sync",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_item_id", sa.Integer()),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
    )

    job_ids = [row[0] for row in bind.execute(sa.select(job.c.id).order_by(job.c.id))]
    if job_ids:
        bind.execute(
            work_item.insert(),
            [{"id": job_id, "kind": "job"} for job_id in job_ids],
        )
    next_id = max(job_ids, default=0) + 1

    sync_rows = bind.execute(
        sa.select(sync.c.id).order_by(sync.c.requested_at.asc(), sync.c.id.asc())
    ).all()
    for row in sync_rows:
        bind.execute(
            work_item.insert().values(id=next_id, kind="environment_sync")
        )
        bind.execute(
            sync.update().where(sync.c.id == row.id).values(work_item_id=next_id)
        )
        next_id += 1

    if bind.dialect.name == "postgresql" and next_id > 1:
        bind.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('work_item', 'id'), "
                ":last_id, true)"
            ),
            {"last_id": next_id - 1},
        )

    with op.batch_alter_table("runner_environment_sync") as batch_op:
        batch_op.alter_column(
            "work_item_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_runner_environment_sync_work_item_id",
            "work_item",
            ["work_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_runner_environment_sync_work_item_id",
            ["work_item_id"],
            unique=True,
        )


def downgrade():
    with op.batch_alter_table("runner_environment_sync") as batch_op:
        batch_op.drop_index("ix_runner_environment_sync_work_item_id")
        batch_op.drop_constraint(
            "fk_runner_environment_sync_work_item_id",
            type_="foreignkey",
        )
        batch_op.drop_column("work_item_id")
    op.drop_index("ix_work_item_kind", table_name="work_item")
    op.drop_table("work_item")
