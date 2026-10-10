"""Request parameter cardinality enforcement.

Journeyman treats duplicate values for scalar parameters as ambiguous input
rather than relying on framework "first value wins" behaviour.
"""

import re

from flask import request


_PACKAGE_LAUNCH_PATH = re.compile(r"^/packages/(\d+)/launch/?$")
_PACKAGE_VALUE_FIELD = re.compile(r"^package_value_(\d+)$")


_MULTI_VALUE_FORM_FIELDS = frozenset({
    "composite_source_inventory_ids",
    "step_name",
    "step_repository_id",
    "step_inventory_id",
    "step_environment_id",
    "step_execution_type",
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
    "roles",
    "rights",
    "member_ids",
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


def _allows_package_multiselect(name):
    """Allow repeated values only for a multi-select input on this Package."""
    path_match = _PACKAGE_LAUNCH_PATH.fullmatch(request.path)
    field_match = _PACKAGE_VALUE_FIELD.fullmatch(str(name))
    if not path_match or not field_match or request.method != "POST":
        return False

    # Import lazily: this module is registered during Flask app setup.
    from app import db
    from app.models.project_package import (
        PACKAGE_INPUT_CHOICE,
        ProjectPackageInput,
    )

    package_id = int(path_match.group(1))
    input_id = int(field_match.group(1))
    package_input = db.session.get(ProjectPackageInput, input_id)
    return bool(
        package_input is not None
        and package_input.package_id == package_id
        and package_input.input_type == PACKAGE_INPUT_CHOICE
        and package_input.get_validation().get("multiple") is True
    )


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
                if _allows_package_multiselect(name):
                    continue
                return (
                    {
                        "error": "Duplicate form parameter is not permitted.",
                        "parameter": str(name)[:120],
                    },
                    400,
                )

    return None
