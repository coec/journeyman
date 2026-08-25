import hashlib
import json
import re

from app.services.safe_regex import UnsafeRegexError, safe_fullmatch
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.credential_crypto import load_credential_key
from app.models.project_package import (
    PACKAGE_BINDING_EXTRA_VAR,
    PACKAGE_BINDING_STEP_LIMIT,
    PACKAGE_DISPLAY_CONFIRMATION_CRITICAL,
    PACKAGE_DISPLAY_OPERATIONAL_TARGET,
    PACKAGE_INPUT_BOOLEAN,
    PACKAGE_INPUT_CHOICE,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
    PACKAGE_INPUT_FILE_PATH,
    PACKAGE_INPUT_URL,
    PACKAGE_INPUT_INTEGER,
    PACKAGE_INPUT_PASSWORD,
    PACKAGE_INPUT_TEXT,
)
from app.services.project_package_inputs import (_is_valid_email_address, _is_valid_file_path, _is_valid_url)
from app.services.project_package_execution import (
    PackageExecutionData,
    build_package_execution_data,
    project_package_definition,
)


class PackageLaunchError(ValueError):
    """
    A Package launch request is invalid.
    """


class PackageLaunchTokenError(PackageLaunchError):
    """
    A Package preview token is invalid or expired.
    """



_BUILTIN_RUNTIME_VALUE_PATTERN = re.compile(
    r"{{\s*(user_email)\s*}}"
)


def _substitute_runtime_values(value, runtime_values):
    """Substitute approved Journeyman runtime values in Package data.

    Only explicitly supported scalar placeholders are substituted.  This is
    deliberately not a general Jinja evaluator.
    """

    if isinstance(value, dict):
        return {
            key: _substitute_runtime_values(item, runtime_values)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _substitute_runtime_values(item, runtime_values)
            for item in value
        ]

    if not isinstance(value, str):
        return value

    def replacement(match):
        name = match.group(1)
        replacement_value = str((runtime_values or {}).get(name) or "").strip()
        if not replacement_value:
            raise PackageLaunchError(
                "Unable to resolve {{{{ {} }}}} for this dispatch.".format(name)
            )
        return replacement_value

    return _BUILTIN_RUNTIME_VALUE_PATTERN.sub(replacement, value)

@dataclass(frozen=True)
class PreparedPackageLaunch:
    fields: list
    execution_data: PackageExecutionData


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_copy(value):
    return json.loads(
        _canonical_json(value)
    )


def _choice_key(value):
    return _canonical_json(value)


def package_definition_digest(package):
    definition = project_package_definition(
        package
    )

    return hashlib.sha256(
        _canonical_json(
            definition
        ).encode("utf-8")
    ).hexdigest()


def _condition_matches(
    rule,
    values_by_variable,
):
    """Evaluate a Package condition expression.

    A normal mapping remains an implicit AND for backward compatibility.
    ``all`` and ``any`` accept lists of nested rules; ``not`` accepts one
    nested rule. Logical operators may be nested.
    """

    if not isinstance(rule, dict):
        return False

    def value_matches(
        actual_value,
        expected_value,
    ):
        if isinstance(expected_value, list):
            return actual_value in expected_value
        return actual_value == expected_value

    results = []

    for key, expected_value in rule.items():
        if key == "all":
            if (
                not isinstance(expected_value, list)
                or not expected_value
            ):
                return False
            results.append(
                all(
                    _condition_matches(
                        nested_rule,
                        values_by_variable,
                    )
                    for nested_rule in expected_value
                )
            )
            continue

        if key == "any":
            if (
                not isinstance(expected_value, list)
                or not expected_value
            ):
                return False
            results.append(
                any(
                    _condition_matches(
                        nested_rule,
                        values_by_variable,
                    )
                    for nested_rule in expected_value
                )
            )
            continue

        if key == "not":
            if not isinstance(expected_value, dict):
                return False
            results.append(
                not _condition_matches(
                    expected_value,
                    values_by_variable,
                )
            )
            continue

        results.append(
            key in values_by_variable
            and value_matches(
                values_by_variable[key],
                expected_value,
            )
        )

    return all(results)


def _format_display_value(
    package_input,
    value,
):
    if (
        package_input.input_type
        == PACKAGE_INPUT_BOOLEAN
    ):
        return "Yes" if value else "No"

    if (
        package_input.input_type
        == PACKAGE_INPUT_EMAIL_ADDRESSES
    ):
        return ", ".join(value)

    if (
        package_input.input_type
        == PACKAGE_INPUT_CHOICE
    ):
        value_key = _choice_key(
            value
        )

        for choice in package_input.get_choices():
            if (
                _choice_key(choice["value"])
                == value_key
            ):
                return choice["label"]

    return str(value)


def _normalise_email_addresses(raw_value, label):
    values = re.split(
        r"[,;\n\r]+",
        str(raw_value or ""),
    )

    email_addresses = []
    seen = set()

    for value in values:
        email_address = value.strip()

        if not email_address:
            continue

        if not _is_valid_email_address(email_address):
            raise PackageLaunchError(
                "{} contains an invalid email address: {}."
                .format(label, email_address)
            )

        key = email_address.casefold()

        if key not in seen:
            seen.add(key)
            email_addresses.append(email_address)

    return email_addresses or None


def _submitted_value(
    package_input,
    form,
    runtime_values=None,
    allowed_choices=None,
):
    field_name = (
        "package_value_{}"
        .format(package_input.id)
    )

    input_type = package_input.input_type

    if input_type == PACKAGE_INPUT_BOOLEAN:
        return (
            form.get(field_name)
            == "true"
        )

    raw_value = form.get(
        field_name,
        "",
    )

    raw_value = _substitute_runtime_values(
        raw_value,
        runtime_values,
    )

    if input_type == PACKAGE_INPUT_EMAIL_ADDRESSES:
        return _normalise_email_addresses(
            raw_value,
            package_input.label,
        )

    if input_type != PACKAGE_INPUT_PASSWORD:
        raw_value = raw_value.strip()

    if not raw_value:
        return None

    if input_type in {PACKAGE_INPUT_TEXT, PACKAGE_INPUT_PASSWORD}:
        return raw_value
    if input_type == PACKAGE_INPUT_URL:
        if not _is_valid_url(raw_value):
            raise PackageLaunchError("{} must be a valid http:// or https:// URL without embedded credentials.".format(package_input.label))
        return raw_value
    if input_type == PACKAGE_INPUT_FILE_PATH:
        if not _is_valid_file_path(raw_value):
            raise PackageLaunchError("{} must be a valid file path.".format(package_input.label))
        return raw_value

    if input_type == PACKAGE_INPUT_INTEGER:
        try:
            return int(raw_value, 10)
        except ValueError as exc:
            raise PackageLaunchError(
                "{} must be a whole number."
                .format(package_input.label)
            ) from exc

    if input_type == PACKAGE_INPUT_CHOICE:
        try:
            value = json.loads(
                raw_value
            )
        except json.JSONDecodeError as exc:
            raise PackageLaunchError(
                "{} contains an invalid choice."
                .format(package_input.label)
            ) from exc

        source_choices = (
            allowed_choices
            if allowed_choices is not None
            else package_input.get_choices()
        )
        allowed_values = {
            _choice_key(choice["value"])
            for choice in source_choices
        }

        if (
            _choice_key(value)
            not in allowed_values
        ):
            raise PackageLaunchError(
                "{} contains an invalid choice."
                .format(package_input.label)
            )

        return value

    raise PackageLaunchError(
        "{} has an unsupported input type."
        .format(package_input.label)
    )


def _validate_value(
    package_input,
    value,
):
    if value is None:
        return []

    errors = []
    validation = (
        package_input.get_validation()
    )

    if package_input.input_type in {
        PACKAGE_INPUT_TEXT,
        PACKAGE_INPUT_PASSWORD,
    }:
        minimum_length = validation.get(
            "minimum_length"
        )

        maximum_length = validation.get(
            "maximum_length"
        )

        pattern = validation.get(
            "pattern"
        )

        if (
            minimum_length is not None
            and len(value) < minimum_length
        ):
            errors.append(
                "{} must contain at least {} characters."
                .format(
                    package_input.label,
                    minimum_length,
                )
            )

        if (
            maximum_length is not None
            and len(value) > maximum_length
        ):
            errors.append(
                "{} must contain no more than {} characters."
                .format(
                    package_input.label,
                    maximum_length,
                )
            )

        if pattern:
            try:
                matched = safe_fullmatch(
                    pattern,
                    value,
                )
            except UnsafeRegexError:
                matched = None

            if matched is None:
                errors.append(
                    "{} does not match the required format."
                    .format(package_input.label)
                )

    elif (
        package_input.input_type
        == PACKAGE_INPUT_INTEGER
    ):
        minimum = validation.get(
            "minimum"
        )

        maximum = validation.get(
            "maximum"
        )

        if (
            minimum is not None
            and value < minimum
        ):
            errors.append(
                "{} must be at least {}."
                .format(
                    package_input.label,
                    minimum,
                )
            )

        if (
            maximum is not None
            and value > maximum
        ):
            errors.append(
                "{} must be no greater than {}."
                .format(
                    package_input.label,
                    maximum,
                )
            )

    return errors


def _path_value(value, path):
    current = value
    for part in str(path or "").split("."):
        part = part.strip()
        if not part:
            return None
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _dynamic_choice_spec(package_input):
    validation = package_input.get_validation()
    spec = validation.get("choices_from_hostvar")
    return spec if isinstance(spec, dict) else None


def _dynamic_choices_by_host(package_input, inventory_hostvars):
    spec = _dynamic_choice_spec(package_input)
    if not spec:
        return {}

    path = str(spec.get("path") or "").strip()
    value_key = str(spec.get("value_key") or "").strip()
    label_keys = [
        str(key).strip()
        for key in (spec.get("label_keys") or [value_key])
        if str(key or "").strip()
    ] or [value_key]

    choices_by_host = {}
    for hostname, hostvars in (inventory_hostvars or {}).items():
        rows = _path_value(hostvars, path)
        if not isinstance(rows, list):
            continue
        seen = set()
        choices = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get(value_key)
            if value is None or str(value).strip() == "":
                continue
            key = _choice_key(value)
            if key in seen:
                continue
            seen.add(key)
            label_parts = [
                str(row.get(label_key) or "").strip()
                for label_key in label_keys
                if str(row.get(label_key) or "").strip()
            ]
            choices.append({
                "key": key,
                "value": value,
                "label": " — ".join(label_parts) or str(value),
            })
        if choices:
            choices_by_host[str(hostname)] = choices
    return choices_by_host


def _choices_for_input(package_input, values_by_variable, inventory_hostvars):
    spec = _dynamic_choice_spec(package_input)
    if not spec:
        return [
            {
                "key": _choice_key(choice["value"]),
                "value": choice["value"],
                "label": choice["label"],
            }
            for choice in package_input.get_choices()
        ]

    host_input = str(spec.get("host_input") or "").strip()
    hostname = values_by_variable.get(host_input)
    if hostname is None:
        return []
    return _dynamic_choices_by_host(
        package_input, inventory_hostvars
    ).get(str(hostname), [])


def _field_for_template(
    package_input,
    *,
    value,
    visible,
    required,
    choices_override=None,
    inventory_hostvars=None,
):
    is_secret = (
        package_input.is_secret
        or package_input.input_type
        == PACKAGE_INPUT_PASSWORD
    )

    if choices_override is None:
        choices = [
            {
                "key": _choice_key(choice["value"]),
                "value": choice["value"],
                "label": choice["label"],
            }
            for choice in package_input.get_choices()
        ]
    else:
        choices = list(choices_override)

    dynamic_spec = _dynamic_choice_spec(package_input)
    dynamic_choices_by_host = (
        _dynamic_choices_by_host(package_input, inventory_hostvars)
        if dynamic_spec
        else {}
    )

    selected_choice_key = ""

    if (
        package_input.input_type
        == PACKAGE_INPUT_CHOICE
        and value is not None
    ):
        selected_choice_key = (
            _choice_key(value)
        )

    template_value = ""

    if (
        value is not None
        and not is_secret
        and package_input.input_type
        not in {
            PACKAGE_INPUT_BOOLEAN,
            PACKAGE_INPUT_CHOICE,
        }
    ):
        if (
            package_input.input_type
            == PACKAGE_INPUT_EMAIL_ADDRESSES
            and isinstance(value, list)
        ):
            template_value = "\n".join(value)
        else:
            template_value = str(value)

    return {
        "id": package_input.id,
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
        "html_name": (
            "package_value_{}"
            .format(package_input.id)
        ),
        "required": required,
        "visible": visible,
        "is_secret": is_secret,
        "display_role": (
            package_input.display_role
        ),
        "binding_type": (
            package_input.binding_type
        ),
        "conditions": (
            package_input.get_conditions()
        ),
        "value": template_value,
        "checked": bool(value),
        "choices": choices,
        "selected_choice_key": (
            selected_choice_key
        ),
        "dynamic_host_input": (
            str(dynamic_spec.get("host_input") or "").strip()
            if dynamic_spec
            else ""
        ),
        "dynamic_choices_by_host": dynamic_choices_by_host,
    }


def package_launch_fields(package, *, runtime_values=None, inventory_hostvars=None):
    """
    Build the initial launch form using Package defaults.
    """

    values_by_variable = {}
    fields = []

    for package_input in package.inputs:
        conditions = (
            package_input.get_conditions()
        )

        visible_when = conditions.get(
            "visible_when"
        )

        required_when = conditions.get(
            "required_when"
        )

        visible = (
            visible_when is None
            or _condition_matches(
                visible_when,
                values_by_variable,
            )
        )

        required = (
            package_input.required
            or (
                visible
                and required_when is not None
                and _condition_matches(
                    required_when,
                    values_by_variable,
                )
            )
        )

        value = None

        if visible:
            value = _substitute_runtime_values(
                package_input.get_default_value(),
                runtime_values,
            )

            if (
                package_input.input_type
                == PACKAGE_INPUT_BOOLEAN
                and value is None
            ):
                value = False

            if value is not None:
                values_by_variable[
                    package_input.variable_name
                ] = value

        current_choices = _choices_for_input(
            package_input, values_by_variable, inventory_hostvars
        )
        fields.append(
            _field_for_template(
                package_input,
                value=value,
                visible=visible,
                required=required,
                choices_override=current_choices,
                inventory_hostvars=inventory_hostvars,
            )
        )

    return fields


def prepare_package_launch(
    *,
    package,
    form,
    runtime_values=None,
    inventory_hostvars=None,
):
    """
    Validate launch answers and construct immutable execution data.
    """

    errors = []
    fields = []
    values_by_variable = {}

    try:
        execution_vars = dict(
            _substitute_runtime_values(
                package.get_fixed_vars(),
                runtime_values,
            )
        )
    except PackageLaunchError as exc:
        return ([str(exc)], package_launch_fields(
            package,
            runtime_values=runtime_values,
            inventory_hostvars=inventory_hostvars,
        ), None)

    display_values = []
    operational_targets = []
    inventory_bindings = {}
    step_limit = ""

    for package_input in package.inputs:
        conditions = (
            package_input.get_conditions()
        )

        visible_when = conditions.get(
            "visible_when"
        )

        required_when = conditions.get(
            "required_when"
        )

        visible = (
            visible_when is None
            or _condition_matches(
                visible_when,
                values_by_variable,
            )
        )

        required = (
            package_input.required
            or (
                visible
                and required_when is not None
                and _condition_matches(
                    required_when,
                    values_by_variable,
                )
            )
        )

        value = None
        submitted_value_valid = True

        current_choices = _choices_for_input(
            package_input, values_by_variable, inventory_hostvars
        )

        if visible:
            try:
                value = _submitted_value(
                    package_input,
                    form,
                    runtime_values,
                    allowed_choices=current_choices,
                )
            except PackageLaunchError as exc:
                errors.append(str(exc))
                submitted_value_valid = False

            if (
                submitted_value_valid
                and required
                and value is None
            ):
                errors.append(
                    "{} is required."
                    .format(package_input.label)
                )

            errors.extend(
                _validate_value(
                    package_input,
                    value,
                )
            )

            if value is not None:
                values_by_variable[
                    package_input.variable_name
                ] = value

                if (
                    package_input.binding_type
                    == PACKAGE_BINDING_EXTRA_VAR
                ):
                    execution_vars[
                        package_input.variable_name
                    ] = value

                elif (
                    package_input.binding_type
                    == PACKAGE_BINDING_STEP_LIMIT
                ):
                    step_limit = str(
                        value
                    ).strip()

                if package_input.bind_to_inventory:
                    binding_name = (
                        package_input.inventory_binding_name
                        or package_input.variable_name
                    )
                    inventory_bindings[binding_name] = value

                is_secret = (
                    package_input.is_secret
                    or package_input.input_type
                    == PACKAGE_INPUT_PASSWORD
                )

                if not is_secret:
                    display_value = (
                        _format_display_value(
                            package_input,
                            value,
                        )
                    )

                    display_values.append(
                        {
                            "variable_name": (
                                package_input
                                .variable_name
                            ),
                            "label": (
                                package_input.label
                            ),
                            "value": value,
                            "display_value": (
                                display_value
                            ),
                            "display_role": (
                                package_input
                                .display_role
                            ),
                            "binding_type": (
                                package_input
                                .binding_type
                            ),
                            "is_secret": False,
                        }
                    )

                    if (
                        package_input.display_role
                        == PACKAGE_DISPLAY_OPERATIONAL_TARGET
                    ):
                        operational_targets.append(
                            display_value
                        )

        fields.append(
            _field_for_template(
                package_input,
                value=value,
                visible=visible,
                required=required,
                choices_override=current_choices,
                inventory_hostvars=inventory_hostvars,
            )
        )

    if errors:
        return (
            errors,
            fields,
            None,
        )

    execution_data = (
        build_package_execution_data(
            package=package,
            execution_vars=execution_vars,
            display_values=display_values,
            operational_targets=(
                operational_targets
            ),
            inventory_bindings=(
                inventory_bindings
            ),
            step_limit=step_limit,
        )
    )

    return (
        [],
        fields,
        PreparedPackageLaunch(
            fields=fields,
            execution_data=execution_data,
        ),
    )



def _condition_variable_names(value):
    """Return Package variable names referenced by a condition tree."""

    names = set()

    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"all", "any", "not"}:
                names.update(_condition_variable_names(item))
            else:
                names.add(str(key))
                names.update(_condition_variable_names(item))

    elif isinstance(value, list):
        for item in value:
            names.update(_condition_variable_names(item))

    return names


def _inventory_binding_input_closure(package, binding_names):
    """Return Package inputs required to obtain the requested bindings."""

    binding_names = {str(name) for name in binding_names}
    inputs_by_variable = {
        item.variable_name: item
        for item in package.inputs
    }
    selected = {}

    for item in package.inputs:
        if not item.bind_to_inventory:
            continue
        binding_name = item.inventory_binding_name or item.variable_name
        if binding_name in binding_names:
            selected[item.variable_name] = item

    missing = binding_names - {
        item.inventory_binding_name or item.variable_name
        for item in selected.values()
    }
    if missing:
        raise PackageLaunchError(
            'Package "{}" does not provide inventory binding{} {}.'.format(
                package.name,
                "s" if len(missing) != 1 else "",
                ", ".join('"{}"'.format(name) for name in sorted(missing)),
            )
        )

    pending = list(selected.values())
    while pending:
        item = pending.pop()
        dependency_names = _condition_variable_names(item.get_conditions())
        for variable_name in dependency_names:
            dependency = inputs_by_variable.get(variable_name)
            if dependency is None or variable_name in selected:
                continue
            selected[variable_name] = dependency
            pending.append(dependency)

    return [
        item for item in package.inputs
        if item.variable_name in selected
    ]


def package_inventory_binding_fields(package, binding_names, *, runtime_values=None):
    """Build Inspect-time fields for one Package's inventory bindings."""

    selected_inputs = _inventory_binding_input_closure(package, binding_names)
    values_by_variable = {}
    fields = []

    for package_input in selected_inputs:
        conditions = package_input.get_conditions()
        visible_when = conditions.get("visible_when")
        required_when = conditions.get("required_when")
        visible = (
            visible_when is None
            or _condition_matches(visible_when, values_by_variable)
        )
        required = (
            package_input.required
            or (
                visible
                and required_when is not None
                and _condition_matches(required_when, values_by_variable)
            )
        )

        value = None
        if visible:
            value = _substitute_runtime_values(
                package_input.get_default_value(),
                runtime_values,
            )
            if package_input.input_type == PACKAGE_INPUT_BOOLEAN and value is None:
                value = False
            if value is not None:
                values_by_variable[package_input.variable_name] = value

        fields.append(
            _field_for_template(
                package_input,
                value=value,
                visible=visible,
                required=required,
            )
        )

    return fields


def prepare_inventory_binding_values(
    *,
    package,
    binding_names,
    form,
    runtime_values=None,
):
    """Validate only Package inputs needed to resolve Inventory bindings.

    This intentionally reuses the Package input semantics without requiring
    unrelated launch inputs. Secret Package inputs are not accepted for
    ad-hoc Inventory inspection because their values would otherwise need to
    be retained across host-selection requests.
    """

    selected_inputs = _inventory_binding_input_closure(package, binding_names)
    selected_variables = {item.variable_name for item in selected_inputs}

    if any(
        item.is_secret or item.input_type == PACKAGE_INPUT_PASSWORD
        for item in selected_inputs
    ):
        raise PackageLaunchError(
            "Inventory inspection cannot prompt for secret Package inputs. "
            "Inspect this inventory through the Package execution path instead."
        )

    errors = []
    fields = []
    values_by_variable = {}
    bindings = {}

    for package_input in package.inputs:
        if package_input.variable_name not in selected_variables:
            continue

        conditions = package_input.get_conditions()
        visible_when = conditions.get("visible_when")
        required_when = conditions.get("required_when")
        visible = (
            visible_when is None
            or _condition_matches(visible_when, values_by_variable)
        )
        required = (
            package_input.required
            or (
                visible
                and required_when is not None
                and _condition_matches(required_when, values_by_variable)
            )
        )

        value = None
        submitted_value_valid = True
        if visible:
            try:
                value = _submitted_value(package_input, form, runtime_values)
            except PackageLaunchError as exc:
                errors.append(str(exc))
                submitted_value_valid = False

            if submitted_value_valid and required and value is None:
                errors.append("{} is required.".format(package_input.label))

            errors.extend(_validate_value(package_input, value))

            if value is not None:
                values_by_variable[package_input.variable_name] = value
                if package_input.bind_to_inventory:
                    binding_name = (
                        package_input.inventory_binding_name
                        or package_input.variable_name
                    )
                    if binding_name in binding_names:
                        bindings[binding_name] = value

        fields.append(
            _field_for_template(
                package_input,
                value=value,
                visible=visible,
                required=required,
            )
        )

    unresolved = set(binding_names) - set(bindings)
    if not errors and unresolved:
        errors.append(
            "The selected Package did not provide required inventory binding{} {} "
            "under the supplied conditions.".format(
                "s" if len(unresolved) != 1 else "",
                ", ".join('"{}"'.format(name) for name in sorted(unresolved)),
            )
        )

    return errors, fields, bindings

def create_package_launch_token(
    *,
    execution_data,
    requested_by,
    preview_digest,
):
    payload = {
        "version": 1,
        "package_id": (
            execution_data.package_id
        ),
        "requested_by": str(
            requested_by
        ),
        "package_definition_sha256": (
            hashlib.sha256(
                _canonical_json(
                    execution_data.definition
                ).encode("utf-8")
            ).hexdigest()
        ),
        "preview_digest": (
            preview_digest
        ),
        "execution_vars": (
            execution_data.execution_vars
        ),
        "display_values": (
            execution_data.display_values
        ),
        "operational_targets": (
            execution_data
            .operational_targets
        ),
        "inventory_bindings": (
            execution_data.inventory_bindings
        ),
        "step_limit": (
            execution_data.step_limit
        ),
        "machine_credential_override_id": (
            execution_data.machine_credential_override_id
        ),
    }

    token = Fernet(
        load_credential_key()
    ).encrypt(
        _canonical_json(
            payload
        ).encode("utf-8")
    )

    return token.decode("ascii")


def read_package_launch_token(
    token,
    *,
    expected_package_id,
    expected_username,
):
    token = str(
        token or ""
    ).strip()

    if not token:
        raise PackageLaunchTokenError(
            "The Package preview token is missing."
        )

    ttl_seconds = int(
        current_app.config.get(
            "PACKAGE_LAUNCH_PREVIEW_TTL_SECONDS",
            900,
        )
    )

    ttl_seconds = max(
        ttl_seconds,
        60,
    )

    try:
        serialized = Fernet(
            load_credential_key()
        ).decrypt(
            token.encode("ascii"),
            ttl=ttl_seconds,
        )
    except (
        InvalidToken,
        UnicodeEncodeError,
    ) as exc:
        raise PackageLaunchTokenError(
            "The Package preview expired or is invalid. "
            "Review the Package again."
        ) from exc

    try:
        payload = json.loads(
            serialized.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise PackageLaunchTokenError(
            "The Package preview token is invalid."
        ) from exc

    if not isinstance(payload, dict):
        raise PackageLaunchTokenError(
            "The Package preview token is invalid."
        )

    if payload.get("version") != 1:
        raise PackageLaunchTokenError(
            "The Package preview token version is unsupported."
        )

    if (
        payload.get("package_id")
        != expected_package_id
    ):
        raise PackageLaunchTokenError(
            "The Package preview belongs to another Package."
        )

    if (
        payload.get("requested_by")
        != expected_username
    ):
        raise PackageLaunchTokenError(
            "The Package preview belongs to another user."
        )

    return _json_copy(
        payload
    )


def package_execution_from_token(
    *,
    package,
    payload,
):
    return build_package_execution_data(
        package=package,
        execution_vars=payload.get(
            "execution_vars",
            {},
        ),
        display_values=payload.get(
            "display_values",
            [],
        ),
        operational_targets=payload.get(
            "operational_targets",
            [],
        ),
        inventory_bindings=payload.get(
            "inventory_bindings",
            {},
        ),
        step_limit=payload.get(
            "step_limit",
            "",
        ),
        machine_credential_override_id=payload.get(
            "machine_credential_override_id",
        ),
    )
