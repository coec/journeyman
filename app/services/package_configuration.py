"""Declarative Package configuration shared by API clients."""

from dataclasses import dataclass

import yaml

from app import db
from app.models import Project, ProjectPackage
from app.models.project_package import (
    PACKAGE_ACCESS_RESTRICTED,
    VALID_PACKAGE_ACCESS_MODES,
)
from app.services.project_package_inputs import (
    apply_package_input_rows,
    prune_stale_reactor_mappings,
    validate_package_input_rows,
)
from app.services.configuration_deletion import (
    ConfigurationDeletionError,
    delete_package_with_job_history,
)
from app.services.project_package_permissions import (
    apply_package_permission_rows,
    validate_package_permission_rows,
)


class PackageConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class PackageConfigurationResult:
    package: ProjectPackage | None
    changed: bool
    message: str


def _clean(value):
    return str(value or "").strip()


def _yaml(value):
    if value is None:
        return ""
    return yaml.safe_dump(value, default_flow_style=False, sort_keys=True).strip()


def _input_rows(values):
    rows = []
    for item in values or []:
        if not isinstance(item, dict):
            raise PackageConfigurationError("Package inputs must be mappings.")
        rows.append({
            "variable_name": _clean(item.get("name") or item.get("variable_name")),
            "label": _clean(item.get("label")) or _clean(item.get("name") or item.get("variable_name")),
            "help_text": _clean(item.get("help_text")),
            "input_type": _clean(item.get("type") or item.get("input_type")) or "text",
            "required": bool(item.get("required", False)),
            "is_secret": bool(item.get("secret", item.get("is_secret", False))),
            "default_value_yaml": _yaml(item.get("default")) if "default" in item else "",
            "choices_yaml": _yaml(item.get("choices") or []),
            "validation_yaml": _yaml(item.get("validation") or {}),
            "conditions_yaml": _yaml(item.get("conditions") or {}),
            "display_role": _clean(item.get("display_role")) or "normal",
            "binding_type": _clean(item.get("binding_type")) or "extra_var",
            "bind_to_inventory": bool(item.get("bind_to_inventory", False)),
            "inventory_binding_name": _clean(item.get("inventory_binding_name")),
        })
    return rows


def _permission_rows(values):
    rows = []
    for item in values or []:
        if not isinstance(item, dict):
            raise PackageConfigurationError("Package permissions must be mappings.")
        rows.append({
            "principal_type": _clean(item.get("type") or item.get("principal_type")),
            "principal_name": _clean(item.get("name") or item.get("principal_name")),
        })
    return rows


def package_configuration_document(package):
    return {
        "id": package.id,
        "name": package.name,
        "description": package.description or "",
        "project": package.project.name if package.project else "",
        "enabled": bool(package.enabled),
        "allow_as_reaction": bool(package.allow_as_reaction),
        "access_mode": package.access_mode,
        "warning_message": package.warning_message or "",
        "confirmation_required": bool(package.confirmation_required),
        "confirmation_message": package.confirmation_message or "",
        "fixed_vars": package.get_fixed_vars(),
        "inputs": [
            {
                "name": item.variable_name,
                "label": item.label,
                "help_text": item.help_text or "",
                "type": item.input_type,
                "required": bool(item.required),
                "secret": bool(item.is_secret),
                "default": item.get_default_value(),
                "choices": item.get_choices(),
                "validation": item.get_validation(),
                "conditions": item.get_conditions(),
                "display_role": item.display_role,
                "binding_type": item.binding_type,
                "bind_to_inventory": bool(item.bind_to_inventory),
                "inventory_binding_name": item.inventory_binding_name or "",
            }
            for item in package.inputs
        ],
        "permissions": [
            {"type": item.principal_type, "name": item.principal_name}
            for item in package.permissions
        ],
    }


def configure_package(values, *, owner="system"):
    if not isinstance(values, dict):
        raise PackageConfigurationError("Package configuration must be a mapping.")

    name = _clean(values.get("name"))
    if not name:
        raise PackageConfigurationError("Package name is required.")

    package = ProjectPackage.query.filter_by(name=name).first()
    if package is not None and package.builtin_key:
        raise PackageConfigurationError("Built-in Packages cannot be modified.")

    project_name = _clean(values.get("project"))
    project = Project.query.filter_by(name=project_name).first() if project_name else None
    if project is None:
        raise PackageConfigurationError('Project "{}" does not exist.'.format(project_name))

    access_mode = _clean(values.get("access_mode")) or PACKAGE_ACCESS_RESTRICTED
    if access_mode not in VALID_PACKAGE_ACCESS_MODES:
        raise PackageConfigurationError("Package access mode is invalid.")

    fixed_vars = values.get("fixed_vars") or {}
    if not isinstance(fixed_vars, dict):
        raise PackageConfigurationError("fixed_vars must be a mapping.")

    input_errors, inputs = validate_package_input_rows(_input_rows(values.get("inputs")), fixed_vars)
    permission_errors, permissions = validate_package_permission_rows(_permission_rows(values.get("permissions")))
    errors = input_errors + permission_errors
    if errors:
        raise PackageConfigurationError(" ".join(errors))

    desired = {
        "name": name,
        "description": _clean(values.get("description")),
        "project": project.name,
        "enabled": bool(values.get("enabled", True)),
        "allow_as_reaction": bool(values.get("allow_as_reaction", False)),
        "access_mode": access_mode,
        "warning_message": _clean(values.get("warning_message")),
        "confirmation_required": bool(values.get("confirmation_required", True)),
        "confirmation_message": _clean(values.get("confirmation_message")),
        "fixed_vars": fixed_vars,
        "inputs": [
            {
                "name": row["variable_name"], "label": row["label"], "help_text": row["help_text"],
                "type": row["input_type"], "required": row["required"], "secret": row["is_secret"],
                "default": row["default_value"], "choices": row["choices"], "validation": row["validation"],
                "conditions": row["conditions"], "display_role": row["display_role"],
                "binding_type": row["binding_type"], "bind_to_inventory": row["bind_to_inventory"],
                "inventory_binding_name": row["inventory_binding_name"],
            }
            for row in inputs
        ],
        "permissions": [{"type": row["principal_type"], "name": row["principal_name"]} for row in permissions],
    }

    created = package is None
    if created:
        package = ProjectPackage(name=name, project_id=project.id, owner=_clean(owner) or "system")
        db.session.add(package)
        current = None
    else:
        current_doc = package_configuration_document(package)
        current = {key: current_doc[key] for key in desired}

    changed = created or current != desired
    if not changed:
        return PackageConfigurationResult(package, False, 'Package "{}" is already configured.'.format(name))

    package.name = name
    package.description = desired["description"]
    package.project_id = project.id
    package.enabled = desired["enabled"]
    package.allow_as_reaction = desired["allow_as_reaction"]
    package.access_mode = desired["access_mode"]
    package.warning_message = desired["warning_message"]
    package.confirmation_required = desired["confirmation_required"]
    package.confirmation_message = desired["confirmation_message"]
    package.set_fixed_vars(fixed_vars)
    apply_package_input_rows(package, inputs, db.session)
    prune_stale_reactor_mappings(package, {row["variable_name"] for row in inputs})
    apply_package_permission_rows(package, permissions, db.session)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise PackageConfigurationError("Unable to save Package configuration.") from exc

    return PackageConfigurationResult(
        package, True,
        'Package "{}" {}.'.format(name, "created" if created else "updated"),
    )


def delete_package(name):
    name = _clean(name)
    package = ProjectPackage.query.filter_by(name=name).first()
    if package is None:
        return PackageConfigurationResult(None, False, 'Package "{}" is already absent.'.format(name))
    if package.builtin_key:
        raise PackageConfigurationError("Built-in Packages cannot be deleted.")
    try:
        job_ids, cleanup_errors = delete_package_with_job_history(package)
    except ConfigurationDeletionError as exc:
        raise PackageConfigurationError(str(exc)) from exc
    message = 'Package "{}" deleted with {} associated Job(s).'.format(name, len(job_ids))
    if cleanup_errors:
        message += " Some Job filesystem output could not be removed; check the Journeyman logs."
    return PackageConfigurationResult(None, True, message)
