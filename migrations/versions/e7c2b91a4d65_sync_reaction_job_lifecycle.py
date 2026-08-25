"""synchronise persisted Reaction state with linked Jobs

Revision ID: e7c2b91a4d65
Revises: d4a8c1e7f930
"""
from alembic import op
import sqlalchemy as sa

revision = "e7c2b91a4d65"
down_revision = "d4a8c1e7f930"
branch_labels = None
depends_on = None


_STATUS_MESSAGES = {
    "queued": "Reaction queued as Job #{}.",
    "running": "Reaction running as Job #{}.",
    "cancelling": "Reaction Job #{} is being cancelled.",
    "successful": "Reaction completed successfully as Job #{}.",
    "failed": "Reaction failed as Job #{}.",
    "cancelled": "Reaction cancelled as Job #{}.",
}


def upgrade():
    """Repair Reaction rows created before Job lifecycle synchronisation existed."""

    connection = op.get_bind()
    reaction = sa.table(
        "reaction",
        sa.column("id", sa.Integer),
        sa.column("job_id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("message", sa.Text),
    )
    job = sa.table(
        "job",
        sa.column("id", sa.Integer),
        sa.column("status", sa.String),
    )

    rows = connection.execute(
        sa.select(reaction.c.id, job.c.id.label("job_id"), job.c.status)
        .select_from(reaction.join(job, reaction.c.job_id == job.c.id))
        .where(job.c.status.in_(tuple(_STATUS_MESSAGES)))
    )

    for reaction_id, job_id, job_status in rows:
        connection.execute(
            reaction.update()
            .where(reaction.c.id == reaction_id)
            .values(
                status=job_status,
                message=_STATUS_MESSAGES[job_status].format(job_id),
            )
        )


def downgrade():
    # This migration repairs audit state to reflect the Job that actually ran.
    # Reverting those records to stale pre-upgrade values would be incorrect.
    pass
