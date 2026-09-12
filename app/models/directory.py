from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import validates

from app import db
from app.credential_crypto import (
    decrypt_credential_data,
    encrypt_credential_data,
)


DIRECTORY_SETTING_ID = 1

DEFAULT_ADMIN_GROUP_NAME = "Journeyman Admins"
DEFAULT_USER_GROUP_NAME = "Journeyman Users"


TEAM_SOURCE_LOCAL = "local"
TEAM_SOURCE_DIRECTORY_LEGACY = "directory_legacy"


team_user_account = db.Table(
    "team_user_account",
    db.Column(
        "team_id",
        db.Integer,
        db.ForeignKey("team.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "user_account_id",
        db.Integer,
        db.ForeignKey("user_account.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


def utcnow():
    return datetime.now(timezone.utc)


class DirectorySetting(db.Model):
    """
    Singleton containing Journeyman's LDAP/Active Directory settings.

    The bind password is encrypted with the same host-managed Fernet key
    used by Journeyman Credentials. It is never returned to a form.
    """

    __tablename__ = "directory_setting"

    __table_args__ = (
        db.CheckConstraint(
            "id = 1",
            name="ck_directory_setting_singleton",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
        default=DIRECTORY_SETTING_ID,
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    base_dn = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    user_search_base = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    group_search_base = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    bind_username = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    encrypted_bind_password = db.Column(
        db.LargeBinary,
        nullable=True,
    )

    ca_certificate_path = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    connect_timeout_seconds = db.Column(
        db.Integer,
        nullable=False,
        default=3,
    )

    operation_timeout_seconds = db.Column(
        db.Integer,
        nullable=False,
        default=10,
    )

    administrator_group_name = db.Column(
        db.String(255),
        nullable=False,
        default=DEFAULT_ADMIN_GROUP_NAME,
    )

    user_group_name = db.Column(
        db.String(255),
        nullable=False,
        default=DEFAULT_USER_GROUP_NAME,
    )

    include_nested_groups = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    updated_by = db.Column(
        db.String(255),
        nullable=False,
        default="system",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    servers = db.relationship(
        "DirectoryServer",
        back_populates="settings",
        cascade="all, delete-orphan",
        order_by="DirectoryServer.position",
        passive_deletes=True,
    )

    def set_bind_password(self, password):
        password = str(password or "")

        if not password:
            raise ValueError(
                "LDAP bind password cannot be empty."
            )

        self.encrypted_bind_password = (
            encrypt_credential_data(
                {
                    "password": password,
                }
            )
        )

    def get_bind_password(self):
        if self.encrypted_bind_password is None:
            return ""

        payload = decrypt_credential_data(
            self.encrypted_bind_password
        )

        password = payload.get("password", "")

        if not isinstance(password, str):
            raise ValueError(
                "Stored LDAP bind password is invalid."
            )

        return password

    def has_bind_password(self):
        return self.encrypted_bind_password is not None

    def __repr__(self):
        return (
            "<DirectorySetting enabled={} base_dn={!r}>"
            .format(
                self.enabled,
                self.base_dn,
            )
        )


class DirectoryServer(db.Model):
    """One ordered LDAP/AD server used for failover."""

    __tablename__ = "directory_server"

    __table_args__ = (
        db.UniqueConstraint(
            "directory_setting_id",
            "position",
            name=(
                "uq_directory_server_setting_position"
            ),
        ),
        db.UniqueConstraint(
            "directory_setting_id",
            "host",
            "port",
            name=(
                "uq_directory_server_setting_host_port"
            ),
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    directory_setting_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "directory_setting.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    position = db.Column(
        db.Integer,
        nullable=False,
    )

    host = db.Column(
        db.String(253),
        nullable=False,
    )

    port = db.Column(
        db.Integer,
        nullable=False,
        default=636,
    )

    use_ssl = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    last_test_ok = db.Column(
        db.Boolean,
        nullable=True,
    )

    last_test_message = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    last_test_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    settings = db.relationship(
        "DirectorySetting",
        back_populates="servers",
    )

    @validates("position")
    def validate_position(self, key, value):
        value = int(value)

        if value < 1:
            raise ValueError(
                "Directory server position must be at least 1."
            )

        return value

    @validates("port")
    def validate_port(self, key, value):
        value = int(value)

        if not 1 <= value <= 65535:
            raise ValueError(
                "Directory server port must be between 1 and 65535."
            )

        return value

    def __repr__(self):
        return (
            "<DirectoryServer position={} host={!r} port={}>"
            .format(
                self.position,
                self.host,
                self.port,
            )
        )


class Team(db.Model):
    """Journeyman-local authorization team.

    Existing v1.x Active Directory-backed Teams are retained during the
    v2.0 migration, but membership becomes authoritative in Journeyman.
    Directory metadata is preserved for audit/reference only.
    """

    __tablename__ = "team"

    id = db.Column(db.Integer, primary_key=True)

    # Stable Team identity used by existing Package permission rows.  New
    # local Teams receive a Journeyman-generated UUID.
    object_guid = db.Column(
        db.String(36),
        nullable=False,
        unique=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Legacy directory metadata.  New local Teams use an internal URI so the
    # existing non-null/unique schema remains compatible during migration.
    distinguished_name = db.Column(
        db.String(1000),
        nullable=False,
        unique=True,
        default="",
    )
    sam_account_name = db.Column(db.String(255), nullable=False, default="")
    display_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(1000), nullable=False, default="")
    source_kind = db.Column(
        db.String(32),
        nullable=False,
        default=TEAM_SOURCE_LOCAL,
    )
    created_by = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    members = db.relationship(
        "UserAccount",
        secondary=team_user_account,
        back_populates="teams",
        order_by="UserAccount.username",
    )

    @classmethod
    def new_local(cls, *, display_name, description, created_by):
        object_guid = str(uuid.uuid4())
        return cls(
            object_guid=object_guid,
            distinguished_name="journeyman://team/{}".format(object_guid),
            sam_account_name="",
            display_name=str(display_name or "").strip(),
            description=str(description or "").strip(),
            source_kind=TEAM_SOURCE_LOCAL,
            created_by=created_by,
        )

    def __repr__(self):
        return (
            "<Team id={} display_name={!r} source_kind={!r}>"
            .format(self.id, self.display_name, self.source_kind)
        )
