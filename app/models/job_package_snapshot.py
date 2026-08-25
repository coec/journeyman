import hashlib
import json
from datetime import datetime, timezone

from app import db
from app.credential_crypto import (
    decrypt_credential_data,
    encrypt_credential_data,
)


def utcnow():
    return datetime.now(timezone.utc)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_json_object(
    raw_value,
    field_name,
):
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


def _load_json_list(
    raw_value,
    field_name,
):
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


class JobPackageSnapshot(db.Model):
    """
    Immutable snapshot of the Project Package used to queue a Job.

    Package execution variables are encrypted. Only values explicitly
    classified as non-secret are stored in display_values_json.

    A Job has zero or one Package snapshot. Direct Project runs do not
    create one.
    """

    __tablename__ = "job_package_snapshot"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    package_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "project_package.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    package_name = db.Column(
        db.String(120),
        nullable=False,
    )

    package_owner = db.Column(
        db.String(255),
        nullable=False,
    )

    package_definition_json = db.Column(
        db.Text,
        nullable=False,
    )

    package_definition_sha256 = db.Column(
        db.String(64),
        nullable=False,
    )

    display_values_json = db.Column(
        db.Text,
        nullable=False,
        default="[]",
    )

    operational_targets_json = db.Column(
        db.Text,
        nullable=False,
        default="[]",
    )

    inventory_bindings_json = db.Column(
        db.Text,
        nullable=False,
        default="{}",
    )

    encrypted_extra_vars = db.Column(
        db.LargeBinary,
        nullable=False,
    )

    extra_vars_format_version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    step_limit = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    job = db.relationship(
        "Job",
        back_populates="package_snapshot",
    )

    package = db.relationship(
        "ProjectPackage",
    )

    def set_package_definition(self, definition):
        if not isinstance(definition, dict):
            raise ValueError(
                "Package definition must be a dictionary."
            )

        serialized = _canonical_json(
            definition
        )

        self.package_definition_json = serialized
        self.package_definition_sha256 = (
            hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()
        )

    def get_package_definition(self):
        return _load_json_object(
            self.package_definition_json,
            "package_definition_json",
        )

    def set_display_values(self, values):
        if not isinstance(values, list):
            raise ValueError(
                "Package display values must be a list."
            )

        self.display_values_json = _canonical_json(
            values
        )

    def get_display_values(self):
        return _load_json_list(
            self.display_values_json,
            "display_values_json",
        )

    def set_operational_targets(self, targets):
        if not isinstance(targets, list):
            raise ValueError(
                "Operational targets must be a list."
            )

        self.operational_targets_json = (
            _canonical_json(targets)
        )

    def get_operational_targets(self):
        return _load_json_list(
            self.operational_targets_json,
            "operational_targets_json",
        )

    def set_inventory_bindings(self, values):
        if not isinstance(values, dict):
            raise ValueError(
                "Inventory bindings must be a dictionary."
            )

        self.inventory_bindings_json = _canonical_json(
            values
        )

    def get_inventory_bindings(self):
        return _load_json_object(
            self.inventory_bindings_json,
            "inventory_bindings_json",
        )

    def set_execution_vars(self, values):
        if not isinstance(values, dict):
            raise ValueError(
                "Package execution variables must be a dictionary."
            )

        self.encrypted_extra_vars = (
            encrypt_credential_data(values)
        )

    def get_execution_vars(self):
        values = decrypt_credential_data(
            self.encrypted_extra_vars
        )

        if not isinstance(values, dict):
            raise ValueError(
                "Decrypted Package variables are not a dictionary."
            )

        return values

    def __repr__(self):
        return (
            "<JobPackageSnapshot job_id={} "
            "package_id={} package_name={!r}>"
            .format(
                self.job_id,
                self.package_id,
                self.package_name,
            )
        )
