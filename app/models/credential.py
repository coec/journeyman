from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app import db
from app.credential_crypto import (
    decrypt_credential_data,
    encrypt_credential_data_with_key_id,
)
from app.credential_types import (
    CREDENTIAL_TYPE_MACHINE,
    validate_credential_data,
    validate_credential_type,
)
from app.security_scope import (
    SECURITY_SCOPE_PRIVATE,
    validate_security_scope,
)

def utcnow():
    return datetime.now(timezone.utc)


class Credential(db.Model):
    """
    A credential that may be used by Journeyman project steps.

    Secret values are stored together in encrypted_data. Journeyman
    must never store credential secrets in plaintext or display them
    after they have been saved.

    Security scopes:

    private
        The owner and administrators may use the credential.

    shared
        Explicit sharing will be implemented later. Until then, shared
        credentials behave like private credentials.

    public
        Any authenticated Journeyman user may use the credential.

    Only the owner and administrators may edit or delete a credential,
    regardless of its security scope.
    """

    __tablename__ = "credential"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        nullable=False,
    )

    description = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    owner = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    security_scope = db.Column(
        db.String(20),
        nullable=False,
        default=SECURITY_SCOPE_PRIVATE,
        index=True,
    )

    credential_type = db.Column(
        db.String(40),
        nullable=False,
        default=CREDENTIAL_TYPE_MACHINE,
        index=True,
    )

    # Non-secret login name. Passwords, private keys, become passwords,
    # tokens, and similar values belong inside encrypted_data.
    username = db.Column(
        db.String(255),
        nullable=False,
        default="",
    )

    # Encrypted structured credential data. The encryption format and
    # key management will be added before credentials can be created.
    encrypted_data = db.Column(
        db.LargeBinary,
        nullable=True,
    )

    # Allows the encrypted payload format to evolve without changing
    # this table each time.
    secret_format_version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    credential_key_id = db.Column(
        db.String(64),
        nullable=True,
        index=True,
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

    secret_updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    project_steps = db.relationship(
        "ProjectStep",
        secondary="project_step_credential",
        back_populates="credentials",
    )

    default_projects = db.relationship(
        "Project",
        secondary="project_credential",
        overlaps="credentials",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "owner",
            "name",
            name="uq_credential_owner_name",
        ),
    )

    @validates("security_scope")
    def validate_scope(self, key, value):
        return validate_security_scope(value)

    @validates("credential_type")
    def validate_type(self, key, value):
        return validate_credential_type(value)

    def set_credential_data(self, credential_data):
        """
        Validate and encrypt this credential's structured data.
        """

        validate_credential_data(
            self.credential_type,
            credential_data,
        )

        self.encrypted_data, self.credential_key_id = (
            encrypt_credential_data_with_key_id(credential_data)
        )
        self.secret_format_version = 1
        self.secret_updated_at = utcnow()

    def get_credential_data(self):
        """
        Decrypt and validate this credential's structured data.

        Authorization must be checked by the caller before using this
        method to reveal secrets.
        """

        credential_data = decrypt_credential_data(
            self.encrypted_data,
            self.credential_key_id,
        )

        validate_credential_data(
            self.credential_type,
            credential_data,
        )

        return credential_data

    def clear_credential_data(self):
        """
        Remove all encrypted secret and credential data.
        """

        self.encrypted_data = None
        self.credential_key_id = None
        self.secret_format_version = 1
        self.secret_updated_at = None

    def __repr__(self):
        return (
            f"<Credential id={self.id} "
            f"name={self.name!r} "
            f"owner={self.owner!r} "
            f"scope={self.security_scope!r}>"
        )
