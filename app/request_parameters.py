"""Request parameter cardinality enforcement.

Journeyman treats duplicate values for scalar parameters as ambiguous input
rather than relying on framework "first value wins" behaviour.
"""

import re

from flask import request


_MULTI_VALUE_FORM_FIELDS = frozenset({
    "composite_source_inventory_ids",
    "step_name",
    "step_repository_id",
    "step_inventory_id",
    "step_environment_id",
    "step_playbook",
    "step_limit",
    "step_tags",
    "step_skip_tags",
    "step_extra_vars",
    "step_verbosity",
    "step_failure_behaviour",
    "step_failure_only",
    "step_remote_shell_serial",
    "step_remote_shell_become",
    "step_refresh_repository",
    "step_refresh_inventory_after",
    "step_credentials_override",
    "package_input_row",
    "package_permission_row",
    "credential_ids",
    "match_field",
    "match_operator",
    "match_value",
    "recovery_match_field",
    "recovery_match_operator",
    "recovery_match_value",
    "mapping_variable",
    "mapping_kind",
    "mapping_value",
    "mapping_pattern",
    "runner_ids",
    "capabilities",
    "weekdays",
    "server_host",
    "server_port",
    "server_use_ssl",
    "server_enabled",
})

_MULTI_VALUE_FORM_PATTERNS = (
    re.compile(r"^(?:include|exclude)_(?:group_id|group_match|rule_group|field|parameter|operator|value)$"),
    re.compile(r"^step_\d+_(?:credential_ids|dependency_positions)$"),
)


def form_field_allows_multiple_values(name):
    name = str(name or "")
    if name in _MULTI_VALUE_FORM_FIELDS:
        return True
    return any(pattern.fullmatch(name) for pattern in _MULTI_VALUE_FORM_PATTERNS)


def reject_ambiguous_request_parameters():
    """Return HTTP 400 when a scalar query/form parameter is duplicated."""

    for name in request.args.keys():
        if len(request.args.getlist(name)) > 1:
            return (
                {
                    "error": "Duplicate query parameter is not permitted.",
                    "parameter": str(name)[:120],
                },
                400,
            )

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        for name in request.form.keys():
            if form_field_allows_multiple_values(name):
                continue
            if len(request.form.getlist(name)) > 1:
                return (
                    {
                        "error": "Duplicate form parameter is not permitted.",
                        "parameter": str(name)[:120],
                    },
                    400,
                )

    return None
