"""local teams and package grants

Revision ID: d9f1a4c7e830
Revises: c8e5f0b3d721
"""
from alembic import op
import sqlalchemy as sa

revision = "d9f1a4c7e830"
down_revision = "c8e5f0b3d721"
branch_labels = None
depends_on = None


def _column_names(bind, table_name):
    return {
        column["name"]
        for column in sa.inspect(bind).get_columns(table_name)
    }


def _index_names(bind, table_name):
    return {
        index["name"]
        for index in sa.inspect(bind).get_indexes(table_name)
        if index.get("name")
    }


def _has_foreign_key(
    bind,
    table_name,
    constrained_column,
    referred_table,
):
    for foreign_key in sa.inspect(bind).get_foreign_keys(table_name):
        if (
            foreign_key.get("constrained_columns")
            == [constrained_column]
            and foreign_key.get("referred_table")
            == referred_table
        ):
            return True
    return False


def upgrade():
    bind = op.get_bind()

    # SQLite DDL is non-transactional.  The existence checks below are
    # intentional: an earlier failed attempt at this migration may have
    # successfully created some columns/tables before failing while adding
    # a foreign-key constraint.
    if "source_kind" not in _column_names(bind, "team"):
        op.add_column(
            "team",
            sa.Column(
                "source_kind",
                sa.String(length=32),
                nullable=False,
                server_default="directory_legacy",
            ),
        )

    if "team_user_account" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "team_user_account",
            sa.Column(
                "team_id",
                sa.Integer(),
                sa.ForeignKey("team.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "user_account_id",
                sa.Integer(),
                sa.ForeignKey("user_account.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )

    permission_columns = _column_names(
        bind,
        "project_package_permission",
    )
    has_user_fk = _has_foreign_key(
        bind,
        "project_package_permission",
        "user_account_id",
        "user_account",
    )
    has_team_fk = _has_foreign_key(
        bind,
        "project_package_permission",
        "team_id",
        "team",
    )

    # Batch mode uses SQLite's copy-and-move strategy when constraints
    # cannot be added with ALTER TABLE.  Add plain columns first, then
    # explicit named foreign keys so this also repairs a partially-applied
    # migration where a column exists but its constraint does not.
    with op.batch_alter_table(
        "project_package_permission"
    ) as batch_op:
        if "user_account_id" not in permission_columns:
            batch_op.add_column(
                sa.Column(
                    "user_account_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
        if "team_id" not in permission_columns:
            batch_op.add_column(
                sa.Column(
                    "team_id",
                    sa.Integer(),
                    nullable=True,
                )
            )

        if not has_user_fk:
            batch_op.create_foreign_key(
                "fk_project_package_permission_user_account",
                "user_account",
                ["user_account_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if not has_team_fk:
            batch_op.create_foreign_key(
                "fk_project_package_permission_team",
                "team",
                ["team_id"],
                ["id"],
                ondelete="CASCADE",
            )

    permission_indexes = _index_names(
        bind,
        "project_package_permission",
    )
    if (
        "ix_project_package_permission_user_account_id"
        not in permission_indexes
    ):
        op.create_index(
            "ix_project_package_permission_user_account_id",
            "project_package_permission",
            ["user_account_id"],
        )
    if (
        "ix_project_package_permission_team_id"
        not in permission_indexes
    ):
        op.create_index(
            "ix_project_package_permission_team_id",
            "project_package_permission",
            ["team_id"],
        )

    metadata = sa.MetaData()
    permission = sa.Table(
        "project_package_permission", metadata, autoload_with=bind
    )
    user = sa.Table("user_account", metadata, autoload_with=bind)
    team = sa.Table("team", metadata, autoload_with=bind)

    users_by_name = {
        str(row.username).strip().casefold(): row.id
        for row in bind.execute(sa.select(user.c.id, user.c.username))
    }
    users_by_guid = {
        str(row.directory_object_guid).strip().lower(): row.id
        for row in bind.execute(
            sa.select(user.c.id, user.c.directory_object_guid).where(
                user.c.directory_object_guid.is_not(None)
            )
        )
        if str(row.directory_object_guid or "").strip()
    }

    teams_by_guid = {}
    teams_by_name = {}
    for row in bind.execute(
        sa.select(
            team.c.id,
            team.c.object_guid,
            team.c.display_name,
            team.c.sam_account_name,
        )
    ):
        if str(row.object_guid or "").strip():
            teams_by_guid[str(row.object_guid).strip().lower()] = row.id
        for value in (row.display_name, row.sam_account_name):
            key = str(value or "").strip().casefold()
            if key:
                teams_by_name.setdefault(key, row.id)

    for row in bind.execute(
        sa.select(
            permission.c.id,
            permission.c.principal_type,
            permission.c.principal_name,
            permission.c.principal_object_guid,
        )
    ):
        values = {}
        principal_name = str(row.principal_name or "").strip().casefold()
        principal_guid = str(row.principal_object_guid or "").strip().lower()

        if row.principal_type == "user":
            user_id = users_by_guid.get(principal_guid) if principal_guid else None
            if user_id is None:
                user_id = users_by_name.get(principal_name)
            if user_id is not None:
                values["user_account_id"] = user_id

        elif row.principal_type == "group":
            team_id = teams_by_guid.get(principal_guid) if principal_guid else None
            if team_id is None:
                team_id = teams_by_name.get(principal_name)
            if team_id is not None:
                values["team_id"] = team_id

        if values:
            bind.execute(
                permission.update()
                .where(permission.c.id == row.id)
                .values(**values)
            )


def downgrade():
    bind = op.get_bind()

    indexes = _index_names(
        bind,
        "project_package_permission",
    )
    if "ix_project_package_permission_team_id" in indexes:
        op.drop_index(
            "ix_project_package_permission_team_id",
            table_name="project_package_permission",
        )
    if "ix_project_package_permission_user_account_id" in indexes:
        op.drop_index(
            "ix_project_package_permission_user_account_id",
            table_name="project_package_permission",
        )

    permission_columns = _column_names(
        bind,
        "project_package_permission",
    )
    with op.batch_alter_table(
        "project_package_permission"
    ) as batch_op:
        if "team_id" in permission_columns:
            batch_op.drop_column("team_id")
        if "user_account_id" in permission_columns:
            batch_op.drop_column("user_account_id")

    if "team_user_account" in sa.inspect(bind).get_table_names():
        op.drop_table("team_user_account")

    if "source_kind" in _column_names(bind, "team"):
        with op.batch_alter_table("team") as batch_op:
            batch_op.drop_column("source_kind")
