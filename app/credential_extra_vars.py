"""Restricted credential-scoped extra-variable validation and rendering."""

import re


class CredentialExtraVarsError(ValueError):
    """Raised when credential-scoped extra vars are invalid."""


_ANSIBLE_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"{{\s*(user|passwd)\s*}}")
_ANY_TEMPLATE_RE = re.compile(r"{{|}}|{%|%}|{#|#}")


def validate_credential_extra_vars(value):
    """Validate a credential extra-vars mapping.

    Credential extra vars deliberately support only the small placeholder set
    ``{{ user }}`` and ``{{ passwd }}``.  This is not a general Jinja
    evaluation surface.
    """
    if value is None:
        return {}

    if not isinstance(value, dict):
        raise CredentialExtraVarsError(
            "Credential extra_vars must be a YAML mapping."
        )

    normalised = {}
    for key, item in value.items():
        key = str(key or "").strip()
        if not key or not _ANSIBLE_VARIABLE_RE.fullmatch(key):
            raise CredentialExtraVarsError(
                "Credential extra_vars key {!r} is not a valid Ansible "
                "variable name.".format(key)
            )
        normalised[key] = _validate_value(item, path=key)

    return normalised


def _validate_value(value, *, path):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = _validate_value(
                item,
                path="{}.{}".format(path, key_text),
            )
        return result

    if isinstance(value, list):
        return [
            _validate_value(item, path="{}[{}]".format(path, index))
            for index, item in enumerate(value)
        ]

    if isinstance(value, str):
        remainder = _PLACEHOLDER_RE.sub("", value)
        if _ANY_TEMPLATE_RE.search(remainder):
            raise CredentialExtraVarsError(
                "Credential extra_vars value {} contains unsupported template "
                "syntax. Only {{ user }} and {{ passwd }} are allowed.".format(
                    path
                )
            )
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    raise CredentialExtraVarsError(
        "Credential extra_vars value {} has unsupported type {}.".format(
            path,
            type(value).__name__,
        )
    )


def render_credential_extra_vars(
    mapping,
    *,
    username="",
    password="",
):
    """Render the restricted credential placeholders in a validated mapping."""
    mapping = validate_credential_extra_vars(mapping)

    replacements = {
        "user": str(username or ""),
        "passwd": str(password or ""),
    }

    def render(value):
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        if not isinstance(value, str):
            return value
        return _PLACEHOLDER_RE.sub(
            lambda match: replacements[match.group(1)],
            value,
        )

    return render(mapping)
