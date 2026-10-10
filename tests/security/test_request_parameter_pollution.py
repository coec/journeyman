"""ASVS evidence for HTTP parameter-pollution resistance."""

import pytest
from werkzeug.datastructures import MultiDict

from app.request_parameters import form_field_allows_multiple_values


pytestmark = pytest.mark.security


def test_duplicate_query_parameter_is_rejected(client):
    response = client.get(
        "/projects?q=first&q=second",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 400
    assert response.get_json()["parameter"] == "q"


def test_duplicate_scalar_form_parameter_is_rejected(client):
    response = client.post(
        "/runners/new",
        data=MultiDict(
            [
                ("name", "first"),
                ("name", "second"),
                ("capabilities", "ansible"),
            ]
        ),
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 400
    assert response.get_json()["parameter"] == "name"


def test_declared_multi_value_fields_remain_supported():
    assert form_field_allows_multiple_values("capabilities")
    assert form_field_allows_multiple_values("weekdays")
    assert form_field_allows_multiple_values("include_field")
    assert form_field_allows_multiple_values("step_3_credential_ids")
    assert form_field_allows_multiple_values("step_execution_type")
    assert not form_field_allows_multiple_values("name")
    assert not form_field_allows_multiple_values("owner")


def test_reactor_and_project_repeated_form_fields_are_declared():
    reactor_fields = {
        "match_field", "match_operator", "match_value",
        "recovery_match_field", "recovery_match_operator", "recovery_match_value",
        "mapping_variable", "mapping_kind", "mapping_value", "mapping_pattern",
    }
    assert all(form_field_allows_multiple_values(name) for name in reactor_fields)
    assert form_field_allows_multiple_values("credential_ids")



def test_package_multiselect_cardinality_is_allowed_only_when_declared(app, monkeypatch):
    from types import SimpleNamespace
    from app import db
    from app.models.project_package import PACKAGE_INPUT_CHOICE
    from app.request_parameters import reject_ambiguous_request_parameters

    package_input = SimpleNamespace(
        package_id=18,
        input_type=PACKAGE_INPUT_CHOICE,
        get_validation=lambda: {"multiple": True},
    )
    monkeypatch.setattr(db.session, "get", lambda model, ident: package_input if ident == 132 else None)
    values = MultiDict([("package_value_132", "one"), ("package_value_132", "two")])

    with app.test_request_context("/packages/18/launch", method="POST", data=values):
        assert reject_ambiguous_request_parameters() is None

    # An input belonging to another Package must remain protected.
    with app.test_request_context("/packages/19/launch", method="POST", data=values):
        result = reject_ambiguous_request_parameters()
        assert result[1] == 400

    # The same field must be scalar when multiple selection is disabled.
    package_input.get_validation = lambda: {"multiple": False}
    with app.test_request_context("/packages/18/launch", method="POST", data=values):
        result = reject_ambiguous_request_parameters()
        assert result[1] == 400

    # Never allow repeated Date values.
    package_input.get_validation = lambda: {"multiple": True}
    package_input.input_type = "date"
    with app.test_request_context("/packages/18/launch", method="POST", data=values):
        result = reject_ambiguous_request_parameters()
        assert result[1] == 400
