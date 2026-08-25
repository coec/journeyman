import json
import re
from urllib.parse import urlsplit

from app.services.safe_regex import UnsafeRegexError, validate_safe_regex

import yaml

from app.models import ProjectPackageInput
from app.models.project_package import (
    PACKAGE_BINDING_EXTRA_VAR,
    PACKAGE_BINDING_STEP_LIMIT,
    PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
    PACKAGE_DISPLAY_NORMAL,
    PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    PACKAGE_INPUT_BOOLEAN,
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
    PACKAGE_INPUT_FILE_PATH,
    PACKAGE_INPUT_URL,
    PACKAGE_INPUT_INTEGER,
    PACKAGE_INPUT_PASSWORD,
    PACKAGE_INPUT_TEXT,
    VALID_PACKAGE_BINDING_TYPES,
    VALID_PACKAGE_DISPLAY_ROLES,
    VALID_PACKAGE_INPUT_TYPES,
    VARIABLE_NAME_PATTERN,
)


CONDITION_KEYS = {
    "visible_when",
    "required_when",
}

VALIDATION_KEYS_BY_TYPE = {
    PACKAGE_INPUT_TEXT: {
        "minimum_length",
        "maximum_length",
        "pattern",
    },
    PACKAGE_INPUT_PASSWORD: {
        "minimum_length",
        "maximum_length",
        "pattern",
    },
    PACKAGE_INPUT_EMAIL_ADDRESSES: set(),
    PACKAGE_INPUT_URL: set(),
    PACKAGE_INPUT_FILE_PATH: set(),
    PACKAGE_INPUT_INTEGER: {
        "minimum",
        "maximum",
    },
    PACKAGE_INPUT_BOOLEAN: set(),
    PACKAGE_INPUT_CHOICE: {
        "choices_from_hostvar",
    },
}

SCALAR_TYPES = (
    str,
    int,
    float,
    bool,
)


def _clean(value):
    return str(value or "").strip()


EMAIL_ADDRESS_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


DEFERRED_EMAIL_RUNTIME_VALUES = {
    "{{ user_email }}",
}


def _is_deferred_email_runtime_value(value):
    return str(value or "").strip() in DEFERRED_EMAIL_RUNTIME_VALUES



def _is_valid_url(value):
    value = str(value or "").strip()
    if not value or any(ord(char) < 32 for char in value): return False
    try: parsed = urlsplit(value)
    except ValueError: return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password

def _is_valid_file_path(value):
    value = str(value or "").strip()
    return bool(value) and "\x00" not in value and "\n" not in value and "\r" not in value

def _is_valid_email_address(value):
    value = _clean(value)

    return (
        len(value) <= 254
        and EMAIL_ADDRESS_PATTERN.fullmatch(value)
        is not None
    )


def _yaml_dump(value):
    if value is None:
        return ""

    text = yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()

    if text.endswith("\n..."):
        text = text[:-4].rstrip()

    return text


def _parse_yaml_value(
    raw_value,
    field_label,
    *,
    blank_value=None,
):
    raw_value = str(
        raw_value or ""
    ).strip()

    if not raw_value:
        return blank_value

    try:
        value = yaml.safe_load(
            raw_value
        )
    except yaml.YAMLError as exc:
        raise ValueError(
            "{} contains invalid YAML: {}"
            .format(
                field_label,
                exc,
            )
        ) from exc

    try:
        json.dumps(
            value,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "{} must contain JSON-compatible values."
            .format(field_label)
        ) from exc

    return value


def _parse_yaml_list(
    raw_value,
    field_label,
):
    value = _parse_yaml_value(
        raw_value,
        field_label,
        blank_value=[],
    )

    if not isinstance(value, list):
        raise ValueError(
            "{} must be a YAML list."
            .format(field_label)
        )

    return value


def _parse_yaml_mapping(
    raw_value,
    field_label,
):
    value = _parse_yaml_value(
        raw_value,
        field_label,
        blank_value={},
    )

    if not isinstance(value, dict):
        raise ValueError(
            "{} must be a YAML mapping."
            .format(field_label)
        )

    return value


def package_input_rows_from_request(
    form,
):
    rows = []

    for row_key in form.getlist(
        "package_input_row"
    ):
        row_key = str(row_key)

        prefix = (
            "package_input_{}_"
            .format(row_key)
        )

        rows.append(
            {
                "row_key": row_key,
                "variable_name": _clean(
                    form.get(
                        prefix + "variable_name"
                    )
                ),
                "label": _clean(
                    form.get(
                        prefix + "label"
                    )
                ),
                "help_text": _clean(
                    form.get(
                        prefix + "help_text"
                    )
                ),
                "input_type": _clean(
                    form.get(
                        prefix + "input_type"
                    )
                ),
                "required": (
                    form.get(
                        prefix + "required"
                    )
                    == "on"
                ),
                "is_secret": (
                    form.get(
                        prefix + "is_secret"
                    )
                    == "on"
                ),
                "default_value_yaml": (
                    form.get(
                        prefix
                        + "default_value_yaml",
                        "",
                    )
                ).strip(),
                "choices_yaml": (
                    form.get(
                        prefix + "choices_yaml",
                        "",
                    )
                ).strip(),
                "validation_yaml": (
                    form.get(
                        prefix
                        + "validation_yaml",
                        "",
                    )
                ).strip(),
                "conditions_yaml": (
                    form.get(
                        prefix
                        + "conditions_yaml",
                        "",
                    )
                ).strip(),
                "display_role": _clean(
                    form.get(
                        prefix + "display_role"
                    )
                ),
                "binding_type": _clean(
                    form.get(
                        prefix + "binding_type"
                    )
                ),
                "bind_to_inventory": (
                    form.get(
                        prefix + "bind_to_inventory"
                    )
                    == "on"
                ),
                "inventory_binding_name": _clean(
                    form.get(
                        prefix + "inventory_binding_name"
                    )
                ),
            }
        )

    return rows


def package_input_rows_for_form(
    package,
):
    rows = []

    for index, package_input in enumerate(
        package.inputs,
        start=1,
    ):
        rows.append(
            {
                "row_key": str(index),
                "variable_name": (
                    package_input.variable_name
                ),
                "label": package_input.label,
                "help_text": (
                    package_input.help_text
                ),
                "input_type": (
                    package_input.input_type
                ),
                "required": (
                    package_input.required
                ),
                "is_secret": (
                    package_input.is_secret
                ),
                "default_value_yaml": (
                    _yaml_dump(
                        package_input
                        .get_default_value()
                    )
                ),
                "choices_yaml": (
                    _yaml_dump(
                        package_input.get_choices()
                    )
                ),
                "validation_yaml": (
                    _yaml_dump(
                        package_input
                        .get_validation()
                    )
                ),
                "conditions_yaml": (
                    _yaml_dump(
                        package_input
                        .get_conditions()
                    )
                ),
                "display_role": (
                    package_input.display_role
                ),
                "binding_type": (
                    package_input.binding_type
                ),
                "bind_to_inventory": (
                    package_input.bind_to_inventory
                ),
                "inventory_binding_name": (
                    package_input.inventory_binding_name
                    or package_input.variable_name
                ),
            }
        )

    return rows


def _normalise_choice(
    item,
    field_label,
):
    if isinstance(item, dict):
        unexpected_keys = (
            set(item)
            - {
                "value",
                "label",
            }
        )

        if unexpected_keys:
            raise ValueError(
                "{} choice has unsupported fields: {}."
                .format(
                    field_label,
                    ", ".join(
                        sorted(unexpected_keys)
                    ),
                )
            )

        if "value" not in item:
            raise ValueError(
                "{} choice requires a value."
                .format(field_label)
            )

        value = item["value"]
        label = item.get(
            "label",
            str(value),
        )
    else:
        value = item
        label = str(item)

    if (
        value is None
        or not isinstance(
            value,
            SCALAR_TYPES,
        )
    ):
        raise ValueError(
            "{} choice values must be strings, "
            "numbers or booleans."
            .format(field_label)
        )

    if not isinstance(label, str):
        raise ValueError(
            "{} choice labels must be strings."
            .format(field_label)
        )

    return {
        "value": value,
        "label": label,
    }


def _choice_key(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
    )


def _validate_default_value(
    row_number,
    input_type,
    default_value,
    choices,
):
    if default_value is None:
        return []

    field_label = (
        "Input {} default value"
        .format(row_number)
    )

    if input_type in {
        PACKAGE_INPUT_TEXT,
        PACKAGE_INPUT_PASSWORD,
    }:
        if not isinstance(
            default_value,
            str,
        ):
            return [
                "{} must be text."
                .format(field_label)
            ]

    elif input_type in {PACKAGE_INPUT_URL, PACKAGE_INPUT_FILE_PATH}:
        if not isinstance(default_value, str):
            return ["{} must be text.".format(field_label)]
        validator = _is_valid_url if input_type == PACKAGE_INPUT_URL else _is_valid_file_path
        if not validator(default_value):
            return ["{} is not a valid {}.".format(field_label, "URL" if input_type == PACKAGE_INPUT_URL else "file path")]

    elif input_type == PACKAGE_INPUT_EMAIL_ADDRESSES:
        if isinstance(default_value, str):
            default_values = [default_value]
        elif isinstance(default_value, list):
            default_values = default_value
        else:
            return [
                "{} must be an email address or a list of email addresses."
                .format(field_label)
            ]

        for email_address in default_values:
            if not isinstance(email_address, str):
                return [
                    "{} must contain only email addresses."
                    .format(field_label)
                ]

            email_address = email_address.strip()

            if (
                not _is_deferred_email_runtime_value(email_address)
                and not _is_valid_email_address(email_address)
            ):
                return [
                    "{} contains an invalid email address: {}."
                    .format(field_label, email_address)
                ]

    elif input_type == PACKAGE_INPUT_INTEGER:
        if (
            isinstance(default_value, bool)
            or not isinstance(
                default_value,
                int,
            )
        ):
            return [
                "{} must be an integer."
                .format(field_label)
            ]

    elif input_type == PACKAGE_INPUT_BOOLEAN:
        if not isinstance(
            default_value,
            bool,
        ):
            return [
                "{} must be true or false."
                .format(field_label)
            ]

    elif input_type == PACKAGE_INPUT_CHOICE:
        allowed_values = {
            _choice_key(choice["value"])
            for choice in choices
        }

        if (
            _choice_key(default_value)
            not in allowed_values
        ):
            return [
                "{} must match one of the "
                "configured choices."
                .format(field_label)
            ]

    return []


def _validate_validation_rules(
    row_number,
    input_type,
    validation,
):
    errors = []

    allowed_keys = (
        VALIDATION_KEYS_BY_TYPE[
            input_type
        ]
    )

    unsupported = (
        set(validation)
        - allowed_keys
    )

    if unsupported:
        errors.append(
            "Input {} validation contains unsupported "
            "fields for type {}: {}."
            .format(
                row_number,
                input_type,
                ", ".join(
                    sorted(unsupported)
                ),
            )
        )

    for key in (
        "minimum_length",
        "maximum_length",
    ):
        if key not in validation:
            continue

        value = validation[key]

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            errors.append(
                "Input {} validation {} must be "
                "a non-negative integer."
                .format(
                    row_number,
                    key,
                )
            )

    if (
        "minimum_length" in validation
        and "maximum_length" in validation
        and isinstance(
            validation["minimum_length"],
            int,
        )
        and isinstance(
            validation["maximum_length"],
            int,
        )
        and (
            validation["minimum_length"]
            > validation["maximum_length"]
        )
    ):
        errors.append(
            "Input {} minimum_length cannot exceed "
            "maximum_length."
            .format(row_number)
        )

    for key in (
        "minimum",
        "maximum",
    ):
        if key not in validation:
            continue

        value = validation[key]

        if (
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
        ):
            errors.append(
                "Input {} validation {} must be "
                "numeric."
                .format(
                    row_number,
                    key,
                )
            )

    if (
        "minimum" in validation
        and "maximum" in validation
        and isinstance(
            validation["minimum"],
            (int, float),
        )
        and not isinstance(
            validation["minimum"],
            bool,
        )
        and isinstance(
            validation["maximum"],
            (int, float),
        )
        and not isinstance(
            validation["maximum"],
            bool,
        )
        and (
            validation["minimum"]
            > validation["maximum"]
        )
    ):
        errors.append(
            "Input {} minimum cannot exceed maximum."
            .format(row_number)
        )

    if "pattern" in validation:
        pattern = validation["pattern"]

        if not isinstance(pattern, str):
            errors.append(
                "Input {} validation pattern must be text."
                .format(row_number)
            )
        else:
            try:
                validate_safe_regex(pattern)
            except UnsafeRegexError as exc:
                errors.append(
                    "Input {} validation pattern is unsafe: {}."
                    .format(row_number, exc)
                )

    dynamic_choices = validation.get("choices_from_hostvar")
    if dynamic_choices is not None:
        if not isinstance(dynamic_choices, dict):
            errors.append(
                "Input {} validation choices_from_hostvar must be a mapping."
                .format(row_number)
            )
        else:
            required_fields = {"host_input", "path", "value_key"}
            missing = required_fields - set(dynamic_choices)
            if missing:
                errors.append(
                    "Input {} choices_from_hostvar is missing: {}.".format(
                        row_number, ", ".join(sorted(missing))
                    )
                )
            unsupported_dynamic = set(dynamic_choices) - {
                "host_input", "path", "value_key", "label_keys"
            }
            if unsupported_dynamic:
                errors.append(
                    "Input {} choices_from_hostvar contains unsupported fields: {}."
                    .format(row_number, ", ".join(sorted(unsupported_dynamic)))
                )
            for key in ("host_input", "path", "value_key"):
                if key in dynamic_choices and not str(dynamic_choices[key] or "").strip():
                    errors.append(
                        "Input {} choices_from_hostvar {} must not be blank."
                        .format(row_number, key)
                    )
            label_keys = dynamic_choices.get("label_keys", [])
            if not isinstance(label_keys, list) or any(
                not str(value or "").strip() for value in label_keys
            ):
                errors.append(
                    "Input {} choices_from_hostvar label_keys must be a list of non-blank keys."
                    .format(row_number)
                )

    return errors


def _validate_conditions(
    row_number,
    conditions,
    earlier_variable_names,
):
    errors = []

    unsupported = (
        set(conditions)
        - CONDITION_KEYS
    )

    if unsupported:
        errors.append(
            "Input {} conditions contain unsupported "
            "fields: {}."
            .format(
                row_number,
                ", ".join(
                    sorted(unsupported)
                ),
            )
        )

    def validate_rule(
        condition_name,
        rule,
        *,
        depth=0,
    ):
        if depth > 12:
            errors.append(
                "Input {} condition {} is nested too deeply."
                .format(
                    row_number,
                    condition_name,
                )
            )
            return

        if not isinstance(rule, dict):
            errors.append(
                "Input {} condition {} must be "
                "a YAML mapping."
                .format(
                    row_number,
                    condition_name,
                )
            )
            return

        if not rule:
            errors.append(
                "Input {} condition {} must not be empty."
                .format(
                    row_number,
                    condition_name,
                )
            )
            return

        for variable_name, expected_value in rule.items():
            if variable_name in {"all", "any"}:
                if (
                    not isinstance(expected_value, list)
                    or not expected_value
                ):
                    errors.append(
                        "Input {} condition {} operator {} "
                        "must contain a non-empty YAML list "
                        "of condition mappings."
                        .format(
                            row_number,
                            condition_name,
                            variable_name,
                        )
                    )
                    continue

                for nested_rule in expected_value:
                    validate_rule(
                        condition_name,
                        nested_rule,
                        depth=depth + 1,
                    )
                continue

            if variable_name == "not":
                if not isinstance(expected_value, dict):
                    errors.append(
                        "Input {} condition {} operator not "
                        "must contain a YAML condition mapping."
                        .format(
                            row_number,
                            condition_name,
                        )
                    )
                    continue

                validate_rule(
                    condition_name,
                    expected_value,
                    depth=depth + 1,
                )
                continue

            if (
                not isinstance(variable_name, str)
                or not VARIABLE_NAME_PATTERN.fullmatch(variable_name)
            ):
                errors.append(
                    "Input {} condition {} contains "
                    "an invalid variable name."
                    .format(
                        row_number,
                        condition_name,
                    )
                )
                continue

            if variable_name not in earlier_variable_names:
                errors.append(
                    "Input {} condition {} references "
                    "{} before that input is defined."
                    .format(
                        row_number,
                        condition_name,
                        variable_name,
                    )
                )

            valid_expected_value = (
                expected_value is None
                or isinstance(expected_value, SCALAR_TYPES)
                or (
                    isinstance(expected_value, list)
                    and bool(expected_value)
                    and all(
                        item is None
                        or isinstance(item, SCALAR_TYPES)
                        for item in expected_value
                    )
                )
            )

            if not valid_expected_value:
                errors.append(
                    "Input {} condition {} for {} must compare "
                    "against a scalar value or a non-empty list "
                    "of scalar values."
                    .format(
                        row_number,
                        condition_name,
                        variable_name,
                    )
                )

    for condition_name in CONDITION_KEYS:
        if condition_name not in conditions:
            continue

        validate_rule(
            condition_name,
            conditions[condition_name],
        )

    return errors


def validate_package_input_rows(
    rows,
    fixed_vars,
):
    errors = []
    normalised_rows = []
    variable_names = set()
    inventory_binding_names = set()
    step_limit_count = 0

    for row_number, row in enumerate(
        rows,
        start=1,
    ):
        prefix = "Input {}".format(
            row_number
        )

        variable_name = _clean(
            row.get("variable_name")
        )
        label = _clean(
            row.get("label")
        )
        help_text = _clean(
            row.get("help_text")
        )
        input_type = _clean(
            row.get("input_type")
        )
        display_role = _clean(
            row.get("display_role")
        )
        binding_type = _clean(
            row.get("binding_type")
        )
        bind_to_inventory = bool(
            row.get("bind_to_inventory")
        )
        inventory_binding_name = _clean(
            row.get("inventory_binding_name")
        )
        required = bool(
            row.get("required")
        )
        is_secret = bool(
            row.get("is_secret")
        )

        if not variable_name:
            errors.append(
                "{} variable name is required."
                .format(prefix)
            )
        elif not VARIABLE_NAME_PATTERN.fullmatch(
            variable_name
        ):
            errors.append(
                "{} has an invalid Ansible variable name."
                .format(prefix)
            )
        elif len(variable_name) > 128:
            errors.append(
                "{} variable name exceeds 128 characters."
                .format(prefix)
            )
        elif variable_name in variable_names:
            errors.append(
                "Package input variable {} is duplicated."
                .format(variable_name)
            )
        elif variable_name in fixed_vars:
            errors.append(
                "Package input variable {} conflicts with "
                "a fixed variable."
                .format(variable_name)
            )

        if not label:
            errors.append(
                "{} label is required."
                .format(prefix)
            )
        elif len(label) > 160:
            errors.append(
                "{} label exceeds 160 characters."
                .format(prefix)
            )

        if (
            input_type
            not in VALID_PACKAGE_INPUT_TYPES
        ):
            errors.append(
                "{} has an invalid input type."
                .format(prefix)
            )

        if (
            display_role
            not in VALID_PACKAGE_DISPLAY_ROLES
        ):
            errors.append(
                "{} has an invalid display role."
                .format(prefix)
            )

        if (
            binding_type
            not in VALID_PACKAGE_BINDING_TYPES
        ):
            errors.append(
                "{} has an invalid binding type."
                .format(prefix)
            )

        if bind_to_inventory:
            if is_secret or input_type == PACKAGE_INPUT_PASSWORD:
                errors.append(
                    "{} cannot bind a secret value to inventories."
                    .format(prefix)
                )

            if input_type == PACKAGE_INPUT_EMAIL_ADDRESSES:
                errors.append(
                    "{} cannot bind a list value to inventories."
                    .format(prefix)
                )

            if not inventory_binding_name:
                inventory_binding_name = variable_name

            if (
                not inventory_binding_name
                or not VARIABLE_NAME_PATTERN.fullmatch(
                    inventory_binding_name
                )
            ):
                errors.append(
                    "{} has an invalid inventory binding name."
                    .format(prefix)
                )
            elif len(inventory_binding_name) > 128:
                errors.append(
                    "{} inventory binding name exceeds 128 characters."
                    .format(prefix)
                )
            elif inventory_binding_name in inventory_binding_names:
                errors.append(
                    "Inventory binding {} is duplicated."
                    .format(inventory_binding_name)
                )
            else:
                inventory_binding_names.add(inventory_binding_name)

        if (
            binding_type
            == PACKAGE_BINDING_STEP_LIMIT
        ):
            step_limit_count += 1

            if is_secret:
                errors.append(
                    "{} cannot use a secret value as "
                    "an Ansible limit."
                    .format(prefix)
                )

        try:
            default_value = _parse_yaml_value(
                row.get(
                    "default_value_yaml"
                ),
                "{} default value".format(
                    prefix
                ),
                blank_value=None,
            )
        except ValueError as exc:
            errors.append(str(exc))
            default_value = None

        try:
            raw_choices = _parse_yaml_list(
                row.get("choices_yaml"),
                "{} choices".format(
                    prefix
                ),
            )
        except ValueError as exc:
            errors.append(str(exc))
            raw_choices = []

        choices = []
        choice_keys = set()

        for raw_choice in raw_choices:
            try:
                choice = _normalise_choice(
                    raw_choice,
                    "{} choices".format(
                        prefix
                    ),
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue

            key = _choice_key(
                choice["value"]
            )

            if key in choice_keys:
                errors.append(
                    "{} contains a duplicate choice value."
                    .format(prefix)
                )
                continue

            choice_keys.add(key)
            choices.append(choice)

        if (
            input_type
            != PACKAGE_INPUT_CHOICE
            and raw_choices
        ):
            errors.append(
                "{} defines choices but is not a choice input."
                .format(prefix)
            )

        if input_type in VALID_PACKAGE_INPUT_TYPES:
            errors.extend(
                _validate_default_value(
                    row_number,
                    input_type,
                    default_value,
                    choices,
                )
            )

        if input_type == PACKAGE_INPUT_PASSWORD:
            is_secret = True

        if (
            is_secret
            and default_value is not None
        ):
            errors.append(
                "{} is secret and cannot have a stored "
                "default value."
                .format(prefix)
            )

        try:
            validation = _parse_yaml_mapping(
                row.get(
                    "validation_yaml"
                ),
                "{} validation".format(
                    prefix
                ),
            )
        except ValueError as exc:
            errors.append(str(exc))
            validation = {}

        if input_type in VALID_PACKAGE_INPUT_TYPES:
            errors.extend(
                _validate_validation_rules(
                    row_number,
                    input_type,
                    validation,
                )
            )

        dynamic_choices = validation.get("choices_from_hostvar")
        if input_type == PACKAGE_INPUT_CHOICE:
            if not choices and not isinstance(dynamic_choices, dict):
                errors.append(
                    "{} requires at least one choice or choices_from_hostvar."
                    .format(prefix)
                )
            if choices and isinstance(dynamic_choices, dict):
                errors.append(
                    "{} cannot combine static choices with choices_from_hostvar."
                    .format(prefix)
                )
            if isinstance(dynamic_choices, dict):
                host_input = str(dynamic_choices.get("host_input") or "").strip()
                if host_input and host_input not in variable_names:
                    errors.append(
                        "{} choices_from_hostvar host_input must reference an earlier Package input."
                        .format(prefix)
                    )

        try:
            conditions = _parse_yaml_mapping(
                row.get(
                    "conditions_yaml"
                ),
                "{} conditions".format(
                    prefix
                ),
            )
        except ValueError as exc:
            errors.append(str(exc))
            conditions = {}

        errors.extend(
            _validate_conditions(
                row_number,
                conditions,
                variable_names,
            )
        )

        normalised_rows.append(
            {
                "position": row_number,
                "variable_name": variable_name,
                "label": label,
                "help_text": help_text,
                "input_type": input_type,
                "required": required,
                "is_secret": is_secret,
                "default_value": (
                    default_value
                ),
                "choices": choices,
                "validation": validation,
                "conditions": conditions,
                "display_role": (
                    display_role
                ),
                "binding_type": (
                    binding_type
                ),
                "bind_to_inventory": (
                    bind_to_inventory
                ),
                "inventory_binding_name": (
                    inventory_binding_name
                    if bind_to_inventory
                    else ""
                ),
            }
        )

        if variable_name:
            variable_names.add(
                variable_name
            )

    if step_limit_count > 1:
        errors.append(
            "A Package may define only one step-limit input."
        )

    return (
        errors,
        normalised_rows,
    )



def prune_stale_reactor_mappings(
    package,
    valid_input_names,
):
    """Remove Reactor mappings for Package inputs that no longer exist."""
    valid_input_names = set(valid_input_names)
    changed = 0

    for reactor in package.reactors:
        mappings = reactor.get_mappings()
        pruned = {
            name: mapping
            for name, mapping in mappings.items()
            if name in valid_input_names
        }

        if pruned != mappings:
            reactor.set_mappings(pruned)
            changed += 1

    return changed

def apply_package_input_rows(
    package,
    rows,
    session,
):
    existing_inputs = list(
        package.inputs
    )

    for package_input in existing_inputs:
        session.delete(
            package_input
        )

    if existing_inputs:
        session.flush()

    for row in rows:
        package_input = ProjectPackageInput(
            position=row["position"],
            variable_name=(
                row["variable_name"]
            ),
            label=row["label"],
            help_text=row["help_text"],
            input_type=row["input_type"],
            required=row["required"],
            is_secret=row["is_secret"],
            display_role=(
                row["display_role"]
            ),
            binding_type=(
                row["binding_type"]
            ),
            bind_to_inventory=(
                row["bind_to_inventory"]
            ),
            inventory_binding_name=(
                row["inventory_binding_name"]
            ),
        )

        package_input.set_default_value(
            row["default_value"]
        )
        package_input.set_choices(
            row["choices"]
        )
        package_input.set_validation(
            row["validation"]
        )
        package_input.set_conditions(
            row["conditions"]
        )

        package.inputs.append(
            package_input
        )

    prune_stale_reactor_mappings(
        package,
        {
            row["variable_name"]
            for row in rows
        },
    )
