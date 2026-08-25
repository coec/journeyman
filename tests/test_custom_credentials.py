import pytest

from app.custom_credentials import (
    CustomCredentialError,
    missing_custom_credential_fields,
    render_custom_credential_extra_vars,
    validate_custom_credential_data,
    validate_custom_credential_definition,
)


def _definition():
    return {
        "fields": [
            {"id": "endpoint", "type": "string", "label": "Endpoint"},
            {
                "id": "username",
                "type": "string",
                "label": "Username",
            },
            {
                "id": "password",
                "type": "string",
                "label": "Password",
                "secret": True,
            },
        ],
        "extra_vars": {
            "service_url": "{{ endpoint }}",
            "service_username": "{{ username }}",
            "service_password": "{{ password }}",
        },
    }


def test_custom_credential_definition_and_rendering():
    definition = validate_custom_credential_definition(_definition())
    data = validate_custom_credential_data(
        {
            "definition": definition,
            "values": {
                "endpoint": "https://example.test/api",
                "username": "svc",
                "password": "secret",
            },
        }
    )

    assert missing_custom_credential_fields(data) == []
    assert render_custom_credential_extra_vars(data) == {
        "service_url": "https://example.test/api",
        "service_username": "svc",
        "service_password": "secret",
    }


def test_custom_credential_rejects_unknown_placeholder():
    definition = _definition()
    definition["extra_vars"]["bad"] = "{{ missing }}"

    with pytest.raises(CustomCredentialError, match="unknown field"):
        validate_custom_credential_definition(definition)


def test_custom_credential_rejects_general_jinja():
    definition = _definition()
    definition["extra_vars"]["bad"] = "{{ endpoint | upper }}"

    with pytest.raises(CustomCredentialError, match="unsupported template"):
        validate_custom_credential_definition(definition)


def test_custom_credential_requires_all_values_before_render():
    data = {
        "definition": _definition(),
        "values": {
            "endpoint": "https://example.test/api",
            "username": "svc",
            "password": "",
        },
    }

    assert missing_custom_credential_fields(data) == ["password"]
    with pytest.raises(CustomCredentialError, match="missing required"):
        render_custom_credential_extra_vars(data)
