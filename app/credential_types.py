"""
Common credential-type definitions for Journeyman.
"""

from app.credential_extra_vars import validate_credential_extra_vars
from app.custom_credentials import validate_custom_credential_data

CREDENTIAL_TYPE_MACHINE = "machine"
CREDENTIAL_TYPE_WINDOWS = "windows"
CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES = "environment_variables"
CREDENTIAL_TYPE_VAULT = "vault"
CREDENTIAL_TYPE_SOURCE_CONTROL = "source_control"
CREDENTIAL_TYPE_SATELLITE = "satellite"
CREDENTIAL_TYPE_ZABBIX = "zabbix"
CREDENTIAL_TYPE_URL = "url"
CREDENTIAL_TYPE_CUSTOM = "custom"

VALID_CREDENTIAL_TYPES = frozenset(
    (
        CREDENTIAL_TYPE_MACHINE,
        CREDENTIAL_TYPE_WINDOWS,
        CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
        CREDENTIAL_TYPE_VAULT,
        CREDENTIAL_TYPE_SOURCE_CONTROL,
        CREDENTIAL_TYPE_SATELLITE,
        CREDENTIAL_TYPE_ZABBIX,
        CREDENTIAL_TYPE_URL,
        CREDENTIAL_TYPE_CUSTOM,
    )
)

CREDENTIAL_TYPE_CHOICES = (
    (
        CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
        "Environment variables",
    ),
    (
        CREDENTIAL_TYPE_MACHINE,
        "Machine (Linux/UNIX)",
    ),
    (
        CREDENTIAL_TYPE_WINDOWS,
        "Machine (Windows)",
    ),
    (
        CREDENTIAL_TYPE_SATELLITE,
        "Red Hat Satellite",
    ),
    (
        CREDENTIAL_TYPE_SOURCE_CONTROL,
        "Source control",
    ),
    (
        CREDENTIAL_TYPE_VAULT,
        "Vault",
    ),
    (
        CREDENTIAL_TYPE_URL,
        "URL / API",
    ),
    (
        CREDENTIAL_TYPE_ZABBIX,
        "Zabbix API token (legacy)",
    ),
    (
        CREDENTIAL_TYPE_CUSTOM,
        "Custom",
    ),
)

CREDENTIAL_TYPE_LABELS = dict(CREDENTIAL_TYPE_CHOICES)


CREDENTIAL_FIELDS = {
    CREDENTIAL_TYPE_MACHINE: {
        "secret": frozenset(
            (
                "password",
                "ssh_private_key",
                "ssh_key_passphrase",
                "become_password",
            )
        ),
        "non_secret": frozenset(
            (
                "become_method",
                "become_user",
                "extra_vars",
            )
        ),
    },
    CREDENTIAL_TYPE_WINDOWS: {
        "secret": frozenset(("password",)),
        "non_secret": frozenset(("extra_vars",)),
    },
    CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES: {
        "secret": frozenset(("password",)),
        "non_secret": frozenset(
            (
                "username_environment_variable",
                "secret_environment_variable",
            )
        ),
    },
    CREDENTIAL_TYPE_VAULT: {
        "secret": frozenset(
            (
                "vault_password",
            )
        ),
        "non_secret": frozenset(
            (
                "vault_id",
            )
        ),
    },
    CREDENTIAL_TYPE_SOURCE_CONTROL: {
        "secret": frozenset(
            (
                "password",
                "ssh_private_key",
                "ssh_key_passphrase",
            )
        ),
        "non_secret": frozenset(),
    },
    CREDENTIAL_TYPE_SATELLITE: {
        "secret": frozenset(
            (
                "password",
            )
        ),
        "non_secret": frozenset(
            (
                "host",
            )
        ),
    },
    CREDENTIAL_TYPE_ZABBIX: {
        "secret": frozenset(("token",)),
        "non_secret": frozenset(),
    },
    CREDENTIAL_TYPE_URL: {
        "secret": frozenset(("password", "token")),
        "non_secret": frozenset(
            (
                "url",
                "auth_mode",
                "token_url",
                "scope",
                "token_prefix",
            )
        ),
    },
    CREDENTIAL_TYPE_CUSTOM: {
        "secret": frozenset(("definition", "values")),
        "non_secret": frozenset(),
    },
}


def is_valid_credential_type(value):
    return value in VALID_CREDENTIAL_TYPES


def validate_credential_type(value):
    if not is_valid_credential_type(value):
        raise ValueError(
            "Invalid credential type: {!r}".format(value)
        )

    return value


def credential_secret_fields(credential_type):
    validate_credential_type(credential_type)

    return CREDENTIAL_FIELDS[
        credential_type
    ]["secret"]


def credential_non_secret_fields(credential_type):
    validate_credential_type(credential_type)

    return CREDENTIAL_FIELDS[
        credential_type
    ]["non_secret"]


def allowed_credential_fields(credential_type):
    return (
        credential_secret_fields(credential_type)
        | credential_non_secret_fields(credential_type)
    )


def validate_credential_data(
    credential_type,
    credential_data,
):
    """
    Validate the structure of a credential payload.

    This checks field names only. Required values and detailed content
    validation will be handled when credential forms are added.
    """

    validate_credential_type(credential_type)

    if not isinstance(credential_data, dict):
        raise ValueError(
            "Credential data must be a dictionary."
        )

    unknown_fields = (
        set(credential_data)
        - allowed_credential_fields(credential_type)
    )

    if unknown_fields:
        raise ValueError(
            "Unsupported fields for credential type {!r}: {}".format(
                credential_type,
                ", ".join(sorted(unknown_fields)),
            )
        )

    if "extra_vars" in credential_data:
        validate_credential_extra_vars(credential_data.get("extra_vars"))

    if credential_type == CREDENTIAL_TYPE_CUSTOM:
        validate_custom_credential_data(credential_data)

    return credential_data
