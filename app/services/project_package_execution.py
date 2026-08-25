import json
import re
from dataclasses import dataclass


CONTROL_CHARACTER_PATTERN = re.compile(
    r"[\x00-\x1f\x7f]"
)


class PackageExecutionDataError(ValueError):
    """
    Package launch data cannot safely be snapshotted.
    """


@dataclass(frozen=True)
class PackageExecutionData:
    package_id: int
    package_name: str
    package_owner: str
    definition: dict
    execution_vars: dict
    display_values: list
    operational_targets: list
    inventory_bindings: dict
    step_limit: str = ""
    machine_credential_override_id: int | None = None


def _json_copy(
    value,
    field_name,
):
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        return json.loads(
            serialized
        )

    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PackageExecutionDataError(
            "{} contains values that cannot be stored as JSON."
            .format(field_name)
        ) from exc


def project_package_definition(package):
    """
    Return a non-secret immutable definition of a live Package.

    Secret input values are never included. Secret inputs cannot have
    defaults, but the explicit check below protects old or malformed
    database records as well.
    """

    inputs = []

    for package_input in package.inputs:
        default_value = None

        if not package_input.is_secret:
            default_value = (
                package_input.get_default_value()
            )

        inputs.append(
            {
                "position": package_input.position,
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
                "default_value": (
                    default_value
                ),
                "choices": (
                    package_input.get_choices()
                ),
                "validation": (
                    package_input.get_validation()
                ),
                "conditions": (
                    package_input.get_conditions()
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

    permissions = [
        {
            "principal_type": (
                permission.principal_type
            ),
            "principal_name": (
                permission.principal_name
            ),
        }
        for permission in package.permissions
    ]

    updated_at = None

    if package.updated_at is not None:
        updated_at = (
            package.updated_at.isoformat()
        )

    definition = {
        "package_id": package.id,
        "name": package.name,
        "description": package.description,
        "owner": package.owner,
        "enabled": package.enabled,
        "access_mode": package.access_mode,
        "project": {
            "id": package.project_id,
            "name": (
                package.project.name
                if package.project is not None
                else ""
            ),
        },
        "warning_message": (
            package.warning_message
        ),
        "confirmation_required": (
            package.confirmation_required
        ),
        "confirmation_message": (
            package.confirmation_message
        ),
        "fixed_vars": (
            package.get_fixed_vars()
        ),
        "inputs": inputs,
        "permissions": permissions,
        "updated_at": updated_at,
    }

    return _json_copy(
        definition,
        "Package definition",
    )


def build_package_execution_data(
    *,
    package,
    execution_vars,
    display_values,
    operational_targets,
    inventory_bindings=None,
    step_limit="",
    machine_credential_override_id=None,
):
    """
    Validate and normalise data prepared by a Package launch form.

    This does not validate user answers against Package input rules.
    The launch-form service will do that before calling this function.
    """

    if package is None or package.id is None:
        raise PackageExecutionDataError(
            "A saved Project Package is required."
        )

    if not isinstance(execution_vars, dict):
        raise PackageExecutionDataError(
            "Package execution variables must be a dictionary."
        )

    if not isinstance(display_values, list):
        raise PackageExecutionDataError(
            "Package display values must be a list."
        )

    if not isinstance(
        operational_targets,
        (list, tuple),
    ):
        raise PackageExecutionDataError(
            "Operational targets must be a list."
        )

    if inventory_bindings is None:
        inventory_bindings = {}

    if not isinstance(inventory_bindings, dict):
        raise PackageExecutionDataError(
            "Inventory bindings must be a dictionary."
        )

    normalised_inventory_bindings = _json_copy(
        inventory_bindings,
        "Inventory bindings",
    )

    for name, value in normalised_inventory_bindings.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            name,
        ):
            raise PackageExecutionDataError(
                "Inventory binding names must be valid identifiers."
            )

        if isinstance(value, (dict, list)):
            raise PackageExecutionDataError(
                "Inventory binding {} must contain a scalar value."
                .format(name)
            )

    normalised_display_values = _json_copy(
        display_values,
        "Package display values",
    )

    for position, item in enumerate(
        normalised_display_values,
        start=1,
    ):
        if not isinstance(item, dict):
            raise PackageExecutionDataError(
                "Package display value {} must be a mapping."
                .format(position)
            )

        if item.get("is_secret"):
            raise PackageExecutionDataError(
                "Secret Package values cannot be stored "
                "as display values."
            )

    normalised_targets = []

    for target in operational_targets:
        target = str(
            target or ""
        ).strip()

        if not target:
            continue

        if CONTROL_CHARACTER_PATTERN.search(
            target
        ):
            raise PackageExecutionDataError(
                "An operational target contains "
                "control characters."
            )

        normalised_targets.append(
            target
        )

    step_limit = str(
        step_limit or ""
    ).strip()

    if len(step_limit) > 500:
        raise PackageExecutionDataError(
            "Package step limit exceeds 500 characters."
        )

    if CONTROL_CHARACTER_PATTERN.search(
        step_limit
    ):
        raise PackageExecutionDataError(
            "Package step limit contains control characters."
        )

    if machine_credential_override_id in (None, ""):
        machine_credential_override_id = None
    else:
        try:
            machine_credential_override_id = int(machine_credential_override_id)
        except (TypeError, ValueError) as exc:
            raise PackageExecutionDataError(
                "Machine credential override ID must be an integer."
            ) from exc
        if machine_credential_override_id < 1:
            raise PackageExecutionDataError(
                "Machine credential override ID must be positive."
            )

    return PackageExecutionData(
        package_id=package.id,
        package_name=package.name,
        package_owner=package.owner,
        definition=(
            project_package_definition(
                package
            )
        ),
        execution_vars=_json_copy(
            execution_vars,
            "Package execution variables",
        ),
        display_values=(
            normalised_display_values
        ),
        operational_targets=(
            normalised_targets
        ),
        inventory_bindings=(
            normalised_inventory_bindings
        ),
        step_limit=step_limit,
        machine_credential_override_id=machine_credential_override_id,
    )
