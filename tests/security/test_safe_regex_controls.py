"""ASVS evidence for administrator-defined regular-expression safety."""

import pytest

from app.services.safe_regex import (
    MAX_MATCH_INPUT_LENGTH,
    UnsafeRegexError,
    safe_fullmatch,
    validate_safe_regex,
)


pytestmark = pytest.mark.security


def test_normal_package_validation_pattern_is_supported():
    pattern = r"^[A-Za-z0-9._-]+$"
    assert validate_safe_regex(pattern) == pattern
    assert safe_fullmatch(pattern, "lab01-node_1") is not None


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+$",
        r"(a|aa)+$",
        r"(.*)*$",
        r"^(a+)\1$",
        r"^(?=a).*$",
    ],
)
def test_dangerous_or_advanced_patterns_are_rejected(pattern):
    with pytest.raises(UnsafeRegexError):
        validate_safe_regex(pattern)


def test_regex_input_length_is_bounded_before_matching():
    assert safe_fullmatch(r"a+$", "a" * (MAX_MATCH_INPUT_LENGTH + 1)) is None
