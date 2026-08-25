import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app import db


PACKAGE_ACCESS_RESTRICTED = "restricted"
PACKAGE_ACCESS_AUTHENTICATED = "authenticated"

VALID_PACKAGE_ACCESS_MODES = {
    PACKAGE_ACCESS_RESTRICTED,
    PACKAGE_ACCESS_AUTHENTICATED,
}


PACKAGE_INPUT_TEXT = "text"
PACKAGE_INPUT_INTEGER = "integer"
PACKAGE_INPUT_BOOLEAN = "boolean"
PACKAGE_INPUT_CHOICE = "choice"
PACKAGE_INPUT_PASSWORD = "password"
PACKAGE_INPUT_EMAIL_ADDRESSES = "email_addresses"
PACKAGE_INPUT_URL = "url"
PACKAGE_INPUT_FILE_PATH = "file_path"

VALID_PACKAGE_INPUT_TYPES = {
    PACKAGE_INPUT_TEXT,
    PACKAGE_INPUT_INTEGER,
    PACKAGE_INPUT_BOOLEAN,
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_PASSWORD,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
    PACKAGE_INPUT_URL,
    PACKAGE_INPUT_FILE_PATH,
}


PACKAGE_DISPLAY_NORMAL = "normal"
PACKAGE_DISPLAY_OPERATIONAL_TARGET = "operational_target"
PACKAGE_DISPLAY_CONFIRMATION_CRITICAL = "confirmation_critical"

VALID_PACKAGE_DISPLAY_ROLES = {
    PACKAGE_DISPLAY_NORMAL,
    PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
}


PACKAGE_BINDING_EXTRA_VAR = "extra_var"
PACKAGE_BINDING_STEP_LIMIT = "step_limit"

VALID_PACKAGE_BINDING_TYPES = {
    PACKAGE_BINDING_EXTRA_VAR,
    PACKAGE_BINDING_STEP_LIMIT,
}


PACKAGE_PRINCIPAL_USER = "user"
PACKAGE_PRINCIPAL_GROUP = "group"

VALID_PACKAGE_PRINCIPAL_TYPES = {
    PACKAGE_PRINCIPAL_USER,
    PACKAGE_PRINCIPAL_GROUP,
}


VARIABLE_NAME_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


def utcnow():
    return datetime.now(timezone.utc)


def _dump_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_json_object(raw_value, field_name):
    try:
        value = json.loads(
            raw_value or "{}"
        )

    except json.JSONDecodeError as exc:
        raise ValueError(
            "{} does not contain valid JSON."
            .format(field_name)
        ) from exc

    if not isinstance(value, dict):
        raise ValueError(
            "{} must contain a JSON object."
            .format(field_name)
        )

    return value


def _load_json_list(raw_value, field_name):
    try:
        value = json.loads(
            raw_value or "[]"
        )

    except json.JSONDecodeError as exc:
        raise ValueError(
            "{} does not contain valid JSON."
            .format(field_name)
        ) from exc

    if not isinstance(value, list):
        raise ValueError(
            "{} must contain a JSON list."
            .format(field_name)
        )

    return value


class ProjectPackage(db.Model):
    """
    Controlled, user-facing launch definition for a Project.

    A Package is the controlled user-facing launch layer around a
    Project. It may define fixed non-secret runtime variables, prompted
    inputs, launch permissions, warning text and confirmation behaviour.

    Package launch values are validated and previewed before execution.
    Queued Jobs retain immutable Package snapshots so the launch
    definition and supplied values remain auditable after later edits.
    """

    __tablename__ = "project_package"

    __table_args__ = (
        db.UniqueConstraint(
            "name",
            name="uq_project_package_name",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        nullable=False,
    )

    description = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "project.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    builtin_key = db.Column(
        db.String(120),
        nullable=True,
        unique=True,
        index=True,
    )

    owner = db.Column(
        db.String(255),
        nullable=False,
        default="system",
        index=True,
    )

    access_mode = db.Column(
        db.String(32),
        nullable=False,
        default=PACKAGE_ACCESS_RESTRICTED,
    )

    warning_message = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    confirmation_required = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    confirmation_message = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    fixed_vars_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    allow_as_reaction = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
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

    project = db.relationship(
        "Project",
        back_populates="packages",
    )

    inputs = db.relationship(
        "ProjectPackageInput",
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="ProjectPackageInput.position",
        passive_deletes=True,
    )

    reactors = db.relationship(
        "Reactor",
        back_populates="package",
        passive_deletes=True,
    )

    permissions = db.relationship(
        "ProjectPackagePermission",
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="ProjectPackagePermission.id",
        passive_deletes=True,
    )

    @validates("access_mode")
    def validate_access_mode(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if value not in VALID_PACKAGE_ACCESS_MODES:
            raise ValueError(
                "Invalid Package access mode: {!r}"
                .format(value)
            )

        return value

    def get_fixed_vars(self):
        """
        Return fixed non-secret runtime variables.

        For Ansible execution these become extra variables. Other
        execution types may expose the same declared Package values
        through their supported runtime-variable mechanism.

        Secrets must not be stored in fixed_vars_json. Prompted secret
        inputs and Credential values use the encrypted launch/execution
        paths instead.
        """

        return _load_json_object(
            self.fixed_vars_json,
            "fixed_vars_json",
        )

    def set_fixed_vars(self, values):
        if not isinstance(values, dict):
            raise ValueError(
                "Fixed Package variables must be a dictionary."
            )

        self.fixed_vars_json = _dump_json(
            values
        )

    def __repr__(self):
        return (
            "<ProjectPackage id={} name={!r} project_id={}>"
            .format(
                self.id,
                self.name,
                self.project_id,
            )
        )


class ProjectPackageInput(db.Model):
    """
    One declared, typed value that a Package may request from a user.

    Package launches may accept only declared inputs. They must never
    accept an unrestricted extra-vars dictionary from the browser.
    """

    __tablename__ = "project_package_input"

    __table_args__ = (
        db.UniqueConstraint(
            "package_id",
            "variable_name",
            name=(
                "uq_project_package_input_"
                "package_variable"
            ),
        ),
        db.UniqueConstraint(
            "package_id",
            "position",
            name=(
                "uq_project_package_input_"
                "package_position"
            ),
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    package_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "project_package.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    position = db.Column(
        db.Integer,
        nullable=False,
    )

    variable_name = db.Column(
        db.String(128),
        nullable=False,
    )

    label = db.Column(
        db.String(160),
        nullable=False,
    )

    help_text = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    input_type = db.Column(
        db.String(32),
        nullable=False,
        default=PACKAGE_INPUT_TEXT,
    )

    required = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    is_secret = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    default_value_json = db.Column(
        db.Text,
        nullable=True,
    )

    choices_json = db.Column(
        db.Text,
        nullable=False,
        default="[]",
    )

    validation_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    conditions_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    display_role = db.Column(
        db.String(32),
        nullable=False,
        default=PACKAGE_DISPLAY_NORMAL,
    )

    binding_type = db.Column(
        db.String(32),
        nullable=False,
        default=PACKAGE_BINDING_EXTRA_VAR,
    )

    bind_to_inventory = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    inventory_binding_name = db.Column(
        db.String(128),
        nullable=False,
        default="",
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

    package = db.relationship(
        "ProjectPackage",
        back_populates="inputs",
    )

    @validates("position")
    def validate_position(
        self,
        key,
        value,
    ):
        value = int(value)

        if value < 1:
            raise ValueError(
                "Package input position must be at least 1."
            )

        return value

    @validates("variable_name")
    def validate_variable_name(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if not VARIABLE_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "Invalid Ansible variable name: {!r}"
                .format(value)
            )

        return value

    @validates("input_type")
    def validate_input_type(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if value not in VALID_PACKAGE_INPUT_TYPES:
            raise ValueError(
                "Invalid Package input type: {!r}"
                .format(value)
            )

        return value

    @validates("display_role")
    def validate_display_role(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if value not in VALID_PACKAGE_DISPLAY_ROLES:
            raise ValueError(
                "Invalid Package input display role: {!r}"
                .format(value)
            )

        return value

    @validates("binding_type")
    def validate_binding_type(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if value not in VALID_PACKAGE_BINDING_TYPES:
            raise ValueError(
                "Invalid Package input binding type: {!r}"
                .format(value)
            )

        return value

    def get_default_value(self):
        if self.default_value_json is None:
            return None

        try:
            return json.loads(
                self.default_value_json
            )

        except json.JSONDecodeError as exc:
            raise ValueError(
                "default_value_json does not contain valid JSON."
            ) from exc

    def set_default_value(self, value):
        if value is None:
            self.default_value_json = None
            return

        self.default_value_json = _dump_json(
            value
        )

    def get_choices(self):
        return _load_json_list(
            self.choices_json,
            "choices_json",
        )

    def set_choices(self, values):
        if not isinstance(values, list):
            raise ValueError(
                "Package input choices must be a list."
            )

        self.choices_json = _dump_json(
            values
        )

    def get_validation(self):
        return _load_json_object(
            self.validation_json,
            "validation_json",
        )

    def set_validation(self, values):
        if not isinstance(values, dict):
            raise ValueError(
                "Package input validation must be a dictionary."
            )

        self.validation_json = _dump_json(
            values
        )

    def get_conditions(self):
        return _load_json_object(
            self.conditions_json,
            "conditions_json",
        )

    def set_conditions(self, values):
        if not isinstance(values, dict):
            raise ValueError(
                "Package input conditions must be a dictionary."
            )

        self.conditions_json = _dump_json(
            values
        )

    def __repr__(self):
        return (
            "<ProjectPackageInput id={} package_id={} "
            "variable_name={!r}>"
            .format(
                self.id,
                self.package_id,
                self.variable_name,
            )
        )


class ProjectPackagePermission(db.Model):
    """
    Explicit permission to launch a restricted Project Package.

    Principal identity and Team membership will be supplied by the
    authenticated LDAP/Active Directory identity.
    """

    __tablename__ = "project_package_permission"

    __table_args__ = (
        db.UniqueConstraint(
            "package_id",
            "principal_type",
            "principal_name",
            name=(
                "uq_project_package_permission_"
                "package_principal"
            ),
        ),
        db.Index(
            "ix_project_package_permission_principal",
            "principal_type",
            "principal_name",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    package_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "project_package.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    principal_type = db.Column(
        db.String(32),
        nullable=False,
    )

    principal_name = db.Column(
        db.String(255),
        nullable=False,
    )

    # Stable Active Directory object identifier. Older records may be
    # NULL until they are re-saved through the directory-backed form.
    principal_object_guid = db.Column(
        db.String(36),
        nullable=True,
        index=True,
    )

    # Last known distinguished name, retained for audit/display only.
    principal_dn = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    package = db.relationship(
        "ProjectPackage",
        back_populates="permissions",
    )

    @validates("principal_type")
    def validate_principal_type(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if value not in VALID_PACKAGE_PRINCIPAL_TYPES:
            raise ValueError(
                "Invalid Package permission principal type: {!r}"
                .format(value)
            )

        return value

    @validates("principal_name")
    def validate_principal_name(
        self,
        key,
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if not value:
            raise ValueError(
                "Package permission principal name is required."
            )

        return value

    @validates("principal_object_guid")
    def validate_principal_object_guid(
        self,
        key,
        value,
    ):
        value = str(value or "").strip()

        if not value:
            return None

        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError(
                "Package permission principal object GUID is invalid."
            ) from exc

    @validates("principal_dn")
    def validate_principal_dn(
        self,
        key,
        value,
    ):
        value = str(value or "").strip()

        if len(value) > 1000:
            raise ValueError(
                "Package permission principal DN exceeds 1000 characters."
            )

        if any(
            character in value
            for character in ("\x00", "\r", "\n")
        ):
            raise ValueError(
                "Package permission principal DN contains invalid characters."
            )

        return value

    def __repr__(self):
        return (
            "<ProjectPackagePermission id={} package_id={} "
            "principal_type={!r} principal_name={!r}>"
            .format(
                self.id,
                self.package_id,
                self.principal_type,
                self.principal_name,
            )
        )
