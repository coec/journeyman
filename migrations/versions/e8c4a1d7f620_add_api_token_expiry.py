"""add API token expiry

Revision ID: e8c4a1d7f620
Revises: d6b9f3a1c720
"""
from datetime import timedelta

from alembic import op
import sqlalchemy as sa

revision = 'e8c4a1d7f620'
down_revision = 'd6b9f3a1c720'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('api_token') as batch:
        batch.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('credential') as batch:
        batch.add_column(sa.Column('secret_updated_at', sa.DateTime(timezone=True), nullable=True))

    token_table = sa.table(
        'api_token',
        sa.column('id', sa.Integer()),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('expires_at', sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(token_table.c.id, token_table.c.created_at)):
        connection.execute(
            token_table.update().where(token_table.c.id == row.id).values(
                expires_at=row.created_at + timedelta(days=365)
            )
        )

    credential_table = sa.table(
        'credential',
        sa.column('id', sa.Integer()),
        sa.column('encrypted_data', sa.LargeBinary()),
        sa.column('updated_at', sa.DateTime(timezone=True)),
        sa.column('secret_updated_at', sa.DateTime(timezone=True)),
    )
    for row in connection.execute(sa.select(
        credential_table.c.id, credential_table.c.encrypted_data, credential_table.c.updated_at
    )):
        if row.encrypted_data is not None:
            connection.execute(
                credential_table.update().where(credential_table.c.id == row.id).values(
                    secret_updated_at=row.updated_at
                )
            )

    with op.batch_alter_table('api_token') as batch:
        batch.alter_column('expires_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_index('ix_api_token_expires_at', ['expires_at'], unique=False)


def downgrade():
    with op.batch_alter_table('credential') as batch:
        batch.drop_column('secret_updated_at')
    with op.batch_alter_table('api_token') as batch:
        batch.drop_index('ix_api_token_expires_at')
        batch.drop_column('expires_at')
