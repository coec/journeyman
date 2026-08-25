"""Restricted custom credential schemas and extra-variable injection."""

import re


class CustomCredentialError(ValueError):
    """Raised when a custom credential definition or payload is invalid."""


_ANSIBLE_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_ANY_TEMPLATE_RE = re.compile(r"{{|}}|{%|%}|{#|#}")


def validate_custom_credential_definition(value):
    """Validate and normalise one custom credential definition.

    Custom credentials intentionally support only string fields and direct
    ``{{ field_id }}`` substitution. This is not a general Jinja surface.
    """
    if not isinstance(value, dict):
        raise CustomCredentialError(
            "Custom credential definition must be a YAML mapping."
        )

    raw_fields = value.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise CustomCredentialError(
            "Custom credential definition requires a non-empty fields list."
        )

    fields = []
    field_ids = set()
    for index, raw_field in enumerate(raw_fields, start=1):
        if not isinstance(raw_field, dict):
            raise CustomCredentialError(
                "Custom credential field {} must be a mapping.".format(index)
            )
        field_id = str(raw_field.get("id") or "").strip()
        if not _ANSIBLE_VARIABLE_RE.fullmatch(field_id):
            raise CustomCredentialError(
                "Custom credential field {} has an invalid id {!r}.".format(
                    index, field_id
                )
            )
        if field_id in field_ids:
            raise CustomCredentialError(
                "Custom credential field id {!r} is duplicated.".format(field_id)
            )
        field_type = str(raw_field.get("type") or "string").strip().lower()
        if field_type != "string":
            raise CustomCredentialError(
                "Custom credential field {!r} has unsupported type {!r}; "
                "only string is currently supported.".format(field_id, field_type)
            )
        label = str(raw_field.get("label") or field_id).strip()
        if not label:
            label = field_id
        secret = raw_field.get("secret", False)
        if not isinstance(secret, bool):
            raise CustomCredentialError(
                "Custom credential field {!r} secret must be true or false."
                .format(field_id)
            )
        fields.append(
            {
                "id": field_id,
                "type": "string",
                "label": label,
                "secret": secret,
            }
        )
        field_ids.add(field_id)

    raw_extra_vars = value.get("extra_vars")
    if not isinstance(raw_extra_vars, dict) or not raw_extra_vars:
        raise CustomCredentialError(
            "Custom credential definition requires a non-empty extra_vars mapping."
        )

    extra_vars = {}
    for key, item in raw_extra_vars.items():
        key = str(key or "").strip()
        if not _ANSIBLE_VARIABLE_RE.fullmatch(key):
            raise CustomCredentialError(
                "Custom credential extra_vars key {!r} is not a valid Ansible "
                "variable name.".format(key)
            )
        extra_vars[key] = _validate_injector_value(
            item,
            path=key,
            field_ids=field_ids,
        )

    return {"fields": fields, "extra_vars": extra_vars}


def _validate_injector_value(value, *, path, field_ids):
    if isinstance(value, dict):
        return {
            str(key): _validate_injector_value(
                item,
                path="{}.{}".format(path, key),
                field_ids=field_ids,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _validate_injector_value(
                item,
                path="{}[{}]".format(path, index),
                field_ids=field_ids,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        placeholders = set(_PLACEHOLDER_RE.findall(value))
        unknown = placeholders - field_ids
        if unknown:
            raise CustomCredentialError(
                "Custom credential extra_vars value {} references unknown field(s): {}."
                .format(path, ", ".join(sorted(unknown)))
            )
        remainder = _PLACEHOLDER_RE.sub("", value)
        if _ANY_TEMPLATE_RE.search(remainder):
            raise CustomCredentialError(
                "Custom credential extra_vars value {} contains unsupported template "
                "syntax. Only {{ field_id }} placeholders are allowed.".format(path)
            )
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise CustomCredentialError(
        "Custom credential extra_vars value {} has unsupported type {}.".format(
            path, type(value).__name__
        )
    )


def validate_custom_credential_data(value):
    if not isinstance(value, dict):
        raise CustomCredentialError("Custom credential data must be a mapping.")
    definition = validate_custom_credential_definition(value.get("definition"))
    values = value.get("values")
    if not isinstance(values, dict):
        raise CustomCredentialError("Custom credential values must be a mapping.")
    field_ids = {field["id"] for field in definition["fields"]}
    unknown = set(values) - field_ids
    if unknown:
        raise CustomCredentialError(
            "Custom credential contains unknown value field(s): {}.".format(
                ", ".join(sorted(unknown))
            )
        )
    normalised_values = {}
    for field in definition["fields"]:
        field_id = field["id"]
        raw = values.get(field_id, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise CustomCredentialError(
                "Custom credential field {!r} must contain a string.".format(field_id)
            )
        normalised_values[field_id] = raw
    return {"definition": definition, "values": normalised_values}


def missing_custom_credential_fields(data):
    data = validate_custom_credential_data(data)
    return [
        field["id"]
        for field in data["definition"]["fields"]
        if not data["values"].get(field["id"], "").strip()
    ]


def render_custom_credential_extra_vars(data):
    data = validate_custom_credential_data(data)
    missing = missing_custom_credential_fields(data)
    if missing:
        raise CustomCredentialError(
            "Custom credential is missing required field value(s): {}."
            .format(", ".join(missing))
        )
    values = data["values"]

    def render(value):
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        if not isinstance(value, str):
            return value
        return _PLACEHOLDER_RE.sub(
            lambda match: values[match.group(1)],
            value,
        )

    return render(data["definition"]["extra_vars"])
