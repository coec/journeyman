import pytest

from app.services.project_package_inputs import _validate_conditions
from app.services.project_package_launch import _condition_matches


def test_existing_condition_mapping_remains_implicit_and():
    rule = {
        "desc": True,
        "var_action": "shutnoshut",
    }
    assert _condition_matches(
        rule,
        {"desc": True, "var_action": "shutnoshut"},
    )
    assert not _condition_matches(
        rule,
        {"desc": False, "var_action": "shutnoshut"},
    )


def test_any_and_not_conditions():
    required_rule = {
        "any": [
            {"desc": True},
            {"var_action": "shutnoshut"},
        ]
    }
    assert _condition_matches(
        required_rule,
        {"desc": False, "var_action": "shutnoshut"},
    )
    assert _condition_matches(
        required_rule,
        {"desc": True, "var_action": "shut"},
    )
    assert not _condition_matches(
        required_rule,
        {"desc": False, "var_action": "shut"},
    )

    negative_rule = {
        "not": {
            "var_action": "shutnoshut",
        }
    }
    assert _condition_matches(
        negative_rule,
        {"var_action": "shut"},
    )
    assert not _condition_matches(
        negative_rule,
        {"var_action": "shutnoshut"},
    )


def test_nested_all_any_not_conditions():
    rule = {
        "all": [
            {"desc": True},
            {
                "any": [
                    {"var_action": "shut"},
                    {
                        "not": {
                            "persist": True,
                        }
                    },
                ]
            },
        ]
    }
    assert _condition_matches(
        rule,
        {
            "desc": True,
            "var_action": "noshut",
            "persist": False,
        },
    )
    assert not _condition_matches(
        rule,
        {
            "desc": False,
            "var_action": "shut",
            "persist": False,
        },
    )


def test_condition_validation_accepts_nested_logic():
    errors = _validate_conditions(
        3,
        {
            "required_when": {
                "any": [
                    {"desc": True},
                    {
                        "all": [
                            {"var_action": "shutnoshut"},
                            {
                                "not": {
                                    "persist": True,
                                }
                            },
                        ]
                    },
                ]
            }
        },
        {"desc", "var_action", "persist"},
    )
    assert errors == []


@pytest.mark.parametrize(
    "rule, expected_message",
    [
        (
            {"any": []},
            "must contain a non-empty YAML list",
        ),
        (
            {"all": {"desc": True}},
            "must contain a non-empty YAML list",
        ),
        (
            {"not": ["desc"]},
            "must contain a YAML condition mapping",
        ),
        (
            {"any": [{"future_input": True}]},
            "references future_input before that input is defined",
        ),
    ],
)
def test_condition_validation_rejects_invalid_complex_logic(
    rule,
    expected_message,
):
    errors = _validate_conditions(
        2,
        {"required_when": rule},
        {"desc", "var_action", "persist"},
    )
    assert any(
        expected_message in error
        for error in errors
    )
