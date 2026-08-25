"""Declarative Credential configuration shared by API clients."""

import json
from dataclasses import dataclass
import re

from app import db
from app.credential_types import (
    CREDENTIAL_TYPE_CUSTOM,
    CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
    CREDENTIAL_TYPE_MACHINE,
    CREDENTIAL_TYPE_SATELLITE,
    CREDENTIAL_TYPE_SOURCE_CONTROL,
    CREDENTIAL_TYPE_VAULT,
    CREDENTIAL_TYPE_WINDOWS,
    CREDENTIAL_TYPE_ZABBIX,
    CREDENTIAL_TYPE_URL,
    credential_non_secret_fields,
    credential_secret_fields,
    validate_credential_data,
    validate_credential_type,
)
from app.custom_credentials import missing_custom_credential_fields
from app.models import Credential, Environment, Inventory, Project, Repository
from app.security_scope import SECURITY_SCOPE_PRIVATE, validate_security_scope
from app.services.outbound_security import OutboundSecurityError, validate_outbound_url
from app.services.url_credentials import URLCredentialError, normalise_url_credential_data


_ENVIRONMENT_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENVIRONMENT_VARIABLES = frozenset({
    "PATH", "HOME", "PYTHONPATH", "ANSIBLE_CONFIG", "FLASK_APP", "FLASK_ENV",
})


class CredentialConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CredentialConfigurationResult:
    credential: Credential | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def _normalise_data(credential_type, data):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise CredentialConfigurationError("credential_data must be a mapping.")
    data = dict(data)
    try:
        validate_credential_data(credential_type, data)
    except ValueError as exc:
        raise CredentialConfigurationError(str(exc)) from exc

    if credential_type == CREDENTIAL_TYPE_URL:
        try:
            data = normalise_url_credential_data(data, username="")
        except URLCredentialError:
            # Username-dependent required validation is performed after the
            # top-level username is available in _validate_required().
            pass
    if credential_type == CREDENTIAL_TYPE_SATELLITE and "host" in data:
        try:
            data["host"] = validate_outbound_url(_clean(data["host"]).rstrip("/"), purpose="Satellite")
        except OutboundSecurityError as exc:
            raise CredentialConfigurationError(str(exc)) from exc
    return data


def _validate_required(credential_type, username, data):
    errors = []
    if credential_type in {
        CREDENTIAL_TYPE_MACHINE,
        CREDENTIAL_TYPE_WINDOWS,
        CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
        CREDENTIAL_TYPE_SOURCE_CONTROL,
        CREDENTIAL_TYPE_SATELLITE,
    } and not username:
        errors.append("Username is required for this credential type.")

    if credential_type == CREDENTIAL_TYPE_MACHINE:
        if not data.get("password") and not data.get("ssh_private_key"):
            errors.append("A machine credential requires either a password or an SSH private key.")
    elif credential_type == CREDENTIAL_TYPE_WINDOWS:
        if not data.get("password"):
            errors.append("Password is required for a Windows credential.")
    elif credential_type == CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES:
        if not data.get("password"):
            errors.append("Secret is required for an environment-variable credential.")
        user_var = _clean(data.get("username_environment_variable"))
        secret_var = _clean(data.get("secret_environment_variable"))
        for label, value in (("Username", user_var), ("Secret", secret_var)):
            if not value:
                errors.append("{} environment-variable name is required.".format(label))
            elif not _ENVIRONMENT_VARIABLE_RE.fullmatch(value):
                errors.append("{} environment-variable name is invalid.".format(label))
            elif value in _RESERVED_ENVIRONMENT_VARIABLES:
                errors.append("{} environment-variable name is reserved.".format(label))
        if user_var and secret_var and user_var == secret_var:
            errors.append("Username and secret environment variables must be different.")
    elif credential_type == CREDENTIAL_TYPE_VAULT:
        if not data.get("vault_password"):
            errors.append("Vault password is required.")
    elif credential_type == CREDENTIAL_TYPE_SOURCE_CONTROL:
        if not data.get("password") and not data.get("ssh_private_key"):
            errors.append("A source-control credential requires either a password or an SSH private key.")
    elif credential_type == CREDENTIAL_TYPE_SATELLITE:
        if not data.get("host"):
            errors.append("Satellite URL is required.")
        if not data.get("password"):
            errors.append("Password is required for a Satellite credential.")
    elif credential_type == CREDENTIAL_TYPE_ZABBIX:
        if not data.get("token"):
            errors.append("API token is required for a Zabbix credential.")
    elif credential_type == CREDENTIAL_TYPE_URL:
        try:
            normalise_url_credential_data(data, username=username)
        except URLCredentialError as exc:
            errors.append(str(exc))
    elif credential_type == CREDENTIAL_TYPE_CUSTOM:
        try:
            missing = missing_custom_credential_fields(data)
        except ValueError as exc:
            raise CredentialConfigurationError(str(exc)) from exc
        if missing:
            errors.append("Custom credential is missing required field value(s): {}.".format(", ".join(missing)))

    if errors:
        raise CredentialConfigurationError(" ".join(errors))


def credential_configuration_document(credential):
    data = credential.get_credential_data() if credential.encrypted_data else {}
    non_secret = {
        key: data.get(key)
        for key in sorted(credential_non_secret_fields(credential.credential_type))
        if key in data
    }
    populated_secrets = [
        key for key in sorted(credential_secret_fields(credential.credential_type))
        if data.get(key) not in (None, "")
    ]
    return {
        "id": credential.id,
        "name": credential.name,
        "description": credential.description or "",
        "owner": credential.owner,
        "security_scope": credential.security_scope,
        "credential_type": credential.credential_type,
        "username": credential.username or "",
        "credential_data": non_secret,
        "populated_secret_fields": populated_secrets,
    }


def configure_credential(values, *, owner):
    if not isinstance(values, dict):
        raise CredentialConfigurationError("Credential configuration must be a mapping.")

    name = _clean(values.get("name"))
    if not name:
        raise CredentialConfigurationError("Credential name is required.")

    credential_type = _clean(values.get("credential_type")) or CREDENTIAL_TYPE_MACHINE
    security_scope = _clean(values.get("security_scope")) or SECURITY_SCOPE_PRIVATE
    username = _clean(values.get("username"))
    description = _clean(values.get("description"))
    try:
        validate_credential_type(credential_type)
        validate_security_scope(security_scope)
    except ValueError as exc:
        raise CredentialConfigurationError(str(exc)) from exc

    credential = Credential.query.filter_by(owner=owner, name=name).first()
    created = credential is None
    if created:
        credential = Credential(name=name, owner=owner)
        existing_data = {}
    else:
        if credential.credential_type != credential_type and CREDENTIAL_TYPE_CUSTOM in {
            credential.credential_type, credential_type
        }:
            raise CredentialConfigurationError(
                "Custom credentials cannot be converted to or from another credential type; create a new credential instead."
            )
        existing_data = credential.get_credential_data() if credential.encrypted_data else {}
        if credential.credential_type != credential_type:
            existing_data = {}

    raw_data = values.get("credential_data")
    if (
        credential_type == CREDENTIAL_TYPE_CUSTOM
        and not created
        and raw_data in (None, {})
    ):
        supplied = {}
    else:
        supplied = _normalise_data(credential_type, raw_data)
    desired_data = {
        key: supplied[key]
        for key in credential_non_secret_fields(credential_type)
        if key in supplied
    }
    for key in credential_secret_fields(credential_type):
        if key in supplied:
            desired_data[key] = supplied[key]
        elif key in existing_data:
            desired_data[key] = existing_data[key]

    _validate_required(credential_type, username, desired_data)
    if credential_type == CREDENTIAL_TYPE_URL:
        try:
            desired_data = normalise_url_credential_data(desired_data, username=username)
        except URLCredentialError as exc:
            raise CredentialConfigurationError(str(exc)) from exc
    try:
        validate_credential_data(credential_type, desired_data)
    except ValueError as exc:
        raise CredentialConfigurationError(str(exc)) from exc

    changed = created or any((
        (credential.description or "") != description,
        credential.security_scope != security_scope,
        credential.credential_type != credential_type,
        (credential.username or "") != username,
        existing_data != desired_data,
    ))
    if not changed:
        return CredentialConfigurationResult(
            credential, False, 'Credential "{}" is already configured.'.format(name)
        )

    credential.description = description
    credential.security_scope = security_scope
    credential.credential_type = credential_type
    credential.username = username
    credential.set_credential_data(desired_data)
    if created:
        db.session.add(credential)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise CredentialConfigurationError("Unable to save Credential configuration.") from exc

    return CredentialConfigurationResult(
        credential, True, 'Credential "{}" {}.'.format(name, "created" if created else "updated")
    )


def delete_credential(name, *, owner):
    name = _clean(name)
    if not name:
        raise CredentialConfigurationError("Credential name is required.")
    credential = Credential.query.filter_by(owner=owner, name=name).first()
    if credential is None:
        return CredentialConfigurationResult(None, False, 'Credential "{}" is already absent.'.format(name))

    if credential.project_steps or credential.default_projects:
        raise CredentialConfigurationError(
            'Credential "{}" cannot be deleted because it is used by one or more Projects.'.format(name)
        )
    inventory_used = Inventory.query.filter_by(credential_id=credential.id).first() is not None
    if not inventory_used:
        for inventory in Inventory.query.all():
            try:
                config = json.loads(inventory.config_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if config.get("proxy_credential_id") == credential.id:
                inventory_used = True
                break
    if inventory_used:
        raise CredentialConfigurationError(
            'Credential "{}" cannot be deleted because it is used by an Inventory.'.format(name)
        )
    if Repository.query.filter_by(credential_id=credential.id).first() is not None:
        raise CredentialConfigurationError(
            'Credential "{}" cannot be deleted because it is used by a Repository.'.format(name)
        )

    if Environment.query.filter_by(proxy_credential_id=credential.id).first() is not None:
        raise CredentialConfigurationError(
            'Credential "{}" cannot be deleted because it is used as an Environment build proxy.'.format(name)
        )

    db.session.delete(credential)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise CredentialConfigurationError('Unable to delete Credential "{}".'.format(name)) from exc
    return CredentialConfigurationResult(None, True, 'Credential "{}" deleted.'.format(name))
