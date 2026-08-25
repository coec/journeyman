"""Bounded regular-expression validation for Package inputs.

Package administrators may define validation patterns, so patterns are treated as
untrusted executable complexity. Journeyman deliberately supports a conservative
subset and bounds the input length before invoking Python's backtracking engine.
"""

import re


MAX_PATTERN_LENGTH = 512
MAX_MATCH_INPUT_LENGTH = 4096

_BACKREFERENCE_RE = re.compile(r"(?<!\\)\\(?:[1-9]|g[<{])|\(\?P=")
_UNBOUNDED_GROUP_REPEAT_RE = re.compile(r"\)(?:\*|\+|\{\d+,\})")


class UnsafeRegexError(ValueError):
    """Raised when an administrator-defined pattern violates the safe subset."""


def validate_safe_regex(pattern):
    if not isinstance(pattern, str):
        raise UnsafeRegexError("validation pattern must be text")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise UnsafeRegexError(
            "validation pattern must contain no more than {} characters".format(
                MAX_PATTERN_LENGTH
            )
        )
    if _BACKREFERENCE_RE.search(pattern):
        raise UnsafeRegexError("backreferences are not permitted")
    # Lookaround/conditionals and other extension groups make static complexity
    # analysis considerably less reliable. Non-capturing groups remain useful.
    scrubbed = pattern.replace("(?:", "")
    if "(?" in scrubbed:
        raise UnsafeRegexError(
            "lookaround, conditional, and advanced group constructs are not permitted"
        )
    # Repeating an entire group without an upper bound is the common shape behind
    # catastrophic nested/ambiguous backtracking, e.g. (a+)+ or (a|aa)+.
    if _UNBOUNDED_GROUP_REPEAT_RE.search(pattern):
        raise UnsafeRegexError(
            "unbounded repetition of groups is not permitted; use a bounded quantifier"
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise UnsafeRegexError("validation pattern is invalid: {}".format(exc)) from exc
    return pattern


def safe_fullmatch(pattern, value):
    validate_safe_regex(pattern)
    value = str(value)
    if len(value) > MAX_MATCH_INPUT_LENGTH:
        return None
    return re.fullmatch(pattern, value)
