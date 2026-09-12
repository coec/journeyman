"""make Package access deny-by-default

Revision ID: f1b3c6e9a052
Revises: e0a2b5d8f941
"""

from alembic import op
import sqlalchemy as sa


revision = "f1b3c6e9a052"
down_revision = "e0a2b5d8f941"
branch_labels = None
depends_on = None


def upgrade():
    # v2.0 removes the legacy "all authenticated users" audience. Existing
    # Packages are deliberately converted to restricted access. Their existing
    # explicit User/Team grants are preserved; Packages with no grants become
    # inaccessible to ordinary Users until an Automation Admin delegates them.
    op.execute(
        sa.text(
            "UPDATE project_package "
            "SET access_mode = 'restricted' "
            "WHERE access_mode = 'authenticated'"
        )
    )


def downgrade():
    # The previous audience cannot be reconstructed safely: after upgrade an
    # administrator may have changed explicit grants. Do not silently widen
    # Package access during downgrade.
    pass
