"""Readable assertions for tests that inspect rendered or generated output.

These helpers deliberately print the observed output, the condition being
checked, and the exact pass/fail rule. With pytest's ``-rP`` reporting enabled,
that documentation is retained for passing tests as well as failures.
"""


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _report(*, purpose, actual, expected, pass_rule, fail_rule):
    print("\n[TEST CHECK]")
    print("Purpose: {}".format(purpose))
    print("Looking for: {}".format(expected))
    print("PASS when: {}".format(pass_rule))
    print("FAIL when: {}".format(fail_rule))
    print("Actual output:")
    print(_text(actual))


def assert_output_contains(actual, expected, *, purpose):
    """Assert that rendered/generated output contains an expected value."""
    actual_text = _text(actual)
    expected_text = _text(expected)
    _report(
        purpose=purpose,
        actual=actual_text,
        expected=repr(expected_text),
        pass_rule="the actual output contains the expected text",
        fail_rule="the expected text is absent from the actual output",
    )
    assert expected_text in actual_text, (
        "{}: expected output to contain {!r}".format(purpose, expected_text)
    )


def assert_output_excludes(actual, forbidden, *, purpose):
    """Assert that rendered/generated output does not contain a value."""
    actual_text = _text(actual)
    forbidden_text = _text(forbidden)
    _report(
        purpose=purpose,
        actual=actual_text,
        expected="absence of {!r}".format(forbidden_text),
        pass_rule="the forbidden text is absent from the actual output",
        fail_rule="the forbidden text appears anywhere in the actual output",
    )
    assert forbidden_text not in actual_text, (
        "{}: output unexpectedly contained {!r}".format(
            purpose, forbidden_text
        )
    )


def assert_output_equal(actual, expected, *, purpose):
    """Assert exact output equality while documenting both values."""
    _report(
        purpose=purpose,
        actual=actual,
        expected=repr(expected),
        pass_rule="the actual output exactly equals the expected value",
        fail_rule="the actual output differs from the expected value",
    )
    assert actual == expected, (
        "{}: expected {!r}, got {!r}".format(purpose, expected, actual)
    )
