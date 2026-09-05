import json
import re
import time
from datetime import datetime, timezone
from flask_migrate import Migrate
from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    Blueprint,
    Response,
    stream_with_context,
)
from sqlalchemy import or_
import yaml

from . import csrf, db
from .models.audit_log import AuditLog
from .services.audit import record_audit_event
from .services.environment_build_settings import (
    EnvironmentBuildSettingsError,
    form_data as environment_build_form_data,
    get_or_create_environment_build_settings,
    settings_to_form_data as environment_build_settings_to_form_data,
    test_proxy as test_environment_build_proxy,
    update as update_environment_build_settings,
    validate as validate_environment_build_settings,
)
from .services.environments import (
    EnvironmentBuildError,
    allowed_python_interpreters,
    prepare_managed_environment_build,
    prepare_registered_environment_update,
    delete_managed_environment_files,
    ensure_builtin_environment,
    managed_environment_path,
    validate_environment,
)
from pathlib import Path

from .credential_crypto import CredentialCryptoError
from .credential_types import (
    CREDENTIAL_TYPE_CHOICES,
    CREDENTIAL_TYPE_LABELS,
    CREDENTIAL_TYPE_MACHINE,
    CREDENTIAL_TYPE_WINDOWS,
    CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
    CREDENTIAL_TYPE_SATELLITE,
    CREDENTIAL_TYPE_SOURCE_CONTROL,
    CREDENTIAL_TYPE_VAULT,
    CREDENTIAL_TYPE_ZABBIX,
    CREDENTIAL_TYPE_URL,
    CREDENTIAL_TYPE_CUSTOM,
    VALID_CREDENTIAL_TYPES,
)
from .security_scope import (
    SECURITY_SCOPE_CHOICES,
    VALID_SECURITY_SCOPES,
)
from .services.git import (
    GitError,
    remove_repository_checkout,
    safe_repository_dir,
    sync_repository,
)
from .services.inventory_resolver import (
    InventoryResolutionError,
    inventory_config,
    refresh_inventory,
    resolve_inventory,
)
from .services.composite_inventory import (
    CompositeInventoryError,
    normalise_default_domain,
)
from .services.inventory_cache import (
    InventoryCacheError,
    delete_inventory_cache,
    inventory_host_count,
)
from .services.name_ordering import (
    reserved_name_validation_error,
)
from .services.url_credentials import URLCredentialError, url_credential_details
from .services.outbound_security import (
    OutboundSecurityError,
    validate_outbound_url,
)
from .services.inventory_dependencies import (
    InventoryDependencyError,
    direct_dependants_by_inventory,
    direct_inventory_dependants,
    validate_composite_source_lineages,
    validate_inventory_dependency_update,
)
from .services.job_inventory_snapshot import (
    JobInventorySnapshotError,
    delete_job_inventory_snapshot_path,
    write_job_inventory_snapshot,
)
from .services.project_execution import (
    ProjectExecutionQueueError,
    queue_project_execution,
)
from .services.project_execution_preview import (
    ProjectExecutionPreviewError,
    build_project_execution_preview,
)
from .services.project_package_inputs import (
    apply_package_input_rows,
    package_input_rows_for_form,
    package_input_rows_from_request,
    validate_package_input_rows,
)
from .services.project_package_permissions import (
    apply_package_permission_rows,
    package_permission_rows_for_form,
    package_permission_rows_from_request,
    validate_package_permission_rows,
)
from .services.package_principals import (
    package_principal_context,
)
from .models.system_setting import (
    APPLY_STATUS_APPLIED,
    APPLY_STATUS_FAILED,
    utcnow,
)
from .services.system_status import collect_storage_status, collect_system_status
from .services.runners import (
    authenticate_runner,
    issue_registration_token,
    register_runner,
    runner_health,
)
from .services.system_settings_apply import (
    SystemSettingsApplyError,
    apply_nginx_settings,
)
from .services.system_settings import (
    SystemSettingsValidationError,
    get_or_create_system_settings,
    settings_to_form_data,
    system_settings_form_data,
    update_system_settings,
    validate_system_settings,
)
from .services.directory import (
    DirectoryError,
    get_directory_client,
)
from .services.directory_settings import (
    DirectorySettingsValidationError,
    directory_settings_form_data,
    get_or_create_directory_settings,
    settings_to_form_data as directory_settings_to_form_data,
    update_directory_settings,
    validate_directory_settings,
)
from .services.project_package_launch import (
    PackageLaunchError,
    PackageLaunchTokenError,
    create_package_launch_token,
    package_definition_digest,
    package_execution_from_token,
    package_launch_fields,
    prepare_package_launch,
    read_package_launch_token,
)
from .models import (
    Credential,
    Environment,
    Inventory,
    Job,
    JobCredentialSnapshot,
    JobRepositorySnapshot,
    JobInventorySnapshot,
    JobStep,
    Project,
    ProjectPackage,
    ProjectPackagePermission,
    ProjectStep,
    Repository,
    Runner,
    RunnerCrew,
    Team,
)
from .models.project_package import (
    PACKAGE_ACCESS_AUTHENTICATED,
    PACKAGE_ACCESS_RESTRICTED,
    VALID_PACKAGE_ACCESS_MODES,
    VARIABLE_NAME_PATTERN,
)
from app.auth import (
    can_administer,
    can_launch_package,
    current_user_is_admin,
    current_username,
    can_cancel_job,
    can_view_job,
)

bp = Blueprint("main", __name__)

def _utcnow():
    return datetime.now(timezone.utc)

def _clean(value):
    return (value or "").strip()


_CREDENTIAL_ENVIRONMENT_VARIABLE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)

_RESERVED_CREDENTIAL_ENVIRONMENT_VARIABLES = frozenset(
    {
        "PATH",
        "HOME",
        "PYTHONPATH",
        "ANSIBLE_CONFIG",
        "FLASK_APP",
        "FLASK_ENV",
    }
)


def validate_credential_environment_variables(
    username_variable,
    secret_variable,
):
    errors = []
    values = (
        ("Username environment variable", username_variable),
        ("Secret environment variable", secret_variable),
    )

    for label, value in values:
        if not value:
            errors.append(f"{label} is required.")
        elif not _CREDENTIAL_ENVIRONMENT_VARIABLE_PATTERN.fullmatch(value):
            errors.append(
                f"{label} must be a valid environment-variable name."
            )
        elif value in _RESERVED_CREDENTIAL_ENVIRONMENT_VARIABLES:
            errors.append(f"{label} uses a reserved variable name.")

    if (
        username_variable
        and secret_variable
        and username_variable == secret_variable
    ):
        errors.append(
            "Username and secret environment variables must be different."
        )

    return errors

def _classify_yaml_file(path):
    """
    Classify an Ansible YAML file without preventing its use.

    Returns one of:

        likely_playbook
        likely_non_playbook
        unknown
    """

    try:
        with path.open("r", encoding="utf-8") as handle:
            documents = list(yaml.safe_load_all(handle))
    except (OSError, UnicodeError, yaml.YAMLError):
        return "unknown"

    documents = [
        document
        for document in documents
        if document is not None
    ]

    if not documents:
        return "unknown"

    play_keys = {
        "hosts",
        "import_playbook",
        "tasks",
        "pre_tasks",
        "post_tasks",
        "roles",
    }

    non_playbook_names = {
        "requirements.yml",
        "requirements.yaml",
    }

    if path.name.lower() in non_playbook_names:
        return "likely_non_playbook"

    parts = {part.lower() for part in path.parts}

    if parts.intersection(
        {
            "group_vars",
            "host_vars",
            "defaults",
            "handlers",
            "meta",
            "tasks",
            "vars",
        }
    ):
        return "likely_non_playbook"

    for document in documents:
        if not isinstance(document, list):
            continue

        for item in document:
            if not isinstance(item, dict):
                continue

            if play_keys.intersection(item.keys()):
                return "likely_playbook"

    return "unknown"


def _repository_playbooks(repository):
    """
    Return every YAML file from the repository checkout.

    Files are classified for display purposes, but no YAML file is
    blocked from selection.
    """

    repository_root = safe_repository_dir(
        current_app.config["REPOSITORY_ROOT"],
        repository.id,
    )

    repository_root = Path(repository_root)

    if not repository_root.is_dir():
        return []

    paths = list(repository_root.rglob("*.yml"))
    paths.extend(repository_root.rglob("*.yaml"))

    results = []

    for path in paths:
        if not path.is_file():
            continue

        relative_path = path.relative_to(repository_root)

        if ".git" in relative_path.parts:
            continue

        results.append(
            {
                "path": str(relative_path),
                "classification": _classify_yaml_file(path),
            }
        )

    return sorted(
        results,
        key=lambda item: item["path"].lower(),
    )

def _inventory_config(inventory=None):
    """
    Return the decoded provider configuration for an Inventory.
    """

    if inventory is None or not inventory.config_json:
        return {}

    try:
        value = json.loads(inventory.config_json)
    except (TypeError, ValueError):
        return {}

    if not isinstance(value, dict):
        return {}

    return value

def _inventory_id_from_request():
    """
    Return the submitted project-level inventory ID.
    """

    value = _clean(
        request.form.get("inventory_id")
    )

    if not value:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _validate_project_inventory(inventory_id):
    """
    Validate the project-level inventory selection.
    """

    if inventory_id is None:
        return []

    inventory = db.session.get(
        Inventory,
        inventory_id,
    )

    if inventory is None:
        return ["A valid inventory is required."]

    if not inventory.enabled:
        return [
            f'Inventory "{inventory.name}" is disabled.'
        ]

    return []

FILTER_FIELD_CHOICES = (
    (
        "name",
        "Host name",
    ),
    (
        "group",
        "Host collection / Ansible group",
    ),
    (
        "ansible_host",
        "IP address / ansible_host",
    ),
    (
        "variable",
        "Host variable path",
    ),
    (
        "foreman.host_group",
        "Satellite host group",
    ),
    (
        "foreman.organization",
        "Satellite organization",
    ),
    (
        (
            "foreman.content_attributes."
            "lifecycle_environment_name"
        ),
        "Satellite lifecycle environment",
    ),
    (
        "foreman_facts.distribution::name",
        "Operating system name",
    ),
    (
        "foreman_facts.distribution::version",
        "Operating system version",
    ),
    (
        "foreman_param",
        "Satellite host parameter",
    ),
)

FILTER_OPERATOR_CHOICES = (
    (
        "equals",
        "Equals",
    ),
    (
        "not_equals",
        "Does not equal",
    ),
    (
        "glob",
        "Matches wildcard",
    ),
    (
        "contains",
        "Contains",
    ),
    (
        "starts_with",
        "Starts with",
    ),
    (
        "ends_with",
        "Ends with",
    ),
    (
        "exists",
        "Exists",
    ),
    (
        "not_exists",
        "Does not exist",
    ),
)

FILTER_FIELDS = frozenset(
    value
    for value, _label in FILTER_FIELD_CHOICES
)

FILTER_OPERATORS = frozenset(
    value
    for value, _label in FILTER_OPERATOR_CHOICES
)


def _filter_groups_from_request(prefix):
    """Read grouped filter-rule fields submitted by the form."""

    group_ids = request.form.getlist(
        "{}_group_id".format(prefix)
    )
    group_matches = request.form.getlist(
        "{}_group_match".format(prefix)
    )

    match_by_group = {}
    group_order = []
    for index, group_id in enumerate(group_ids):
        group_id = _clean(group_id)
        if not group_id or group_id in match_by_group:
            continue
        match = _clean(
            group_matches[index]
            if index < len(group_matches)
            else "all"
        ).lower()
        match_by_group[group_id] = match or "all"
        group_order.append(group_id)

    rule_group_ids = request.form.getlist(
        "{}_rule_group".format(prefix)
    )
    fields = request.form.getlist(
        "{}_field".format(prefix)
    )
    parameters = request.form.getlist(
        "{}_parameter".format(prefix)
    )
    operators = request.form.getlist(
        "{}_operator".format(prefix)
    )
    values = request.form.getlist(
        "{}_value".format(prefix)
    )

    rule_count = max(
        len(rule_group_ids),
        len(fields),
        len(parameters),
        len(operators),
        len(values),
        0,
    )

    rules_by_group = {group_id: [] for group_id in group_order}

    for index in range(rule_count):
        group_id = _clean(
            rule_group_ids[index]
            if index < len(rule_group_ids)
            else ""
        )
        field = _clean(
            fields[index]
            if index < len(fields)
            else ""
        )
        parameter = _clean(
            parameters[index]
            if index < len(parameters)
            else ""
        )
        operator = _clean(
            operators[index]
            if index < len(operators)
            else ""
        )
        value = (
            values[index]
            if index < len(values)
            else ""
        ).strip()

        if field not in {"foreman_param", "variable"}:
            parameter = ""

        if operator in {"exists", "not_exists"}:
            value = ""

        if not any((field, parameter, operator, value)):
            continue

        if not group_id:
            group_id = "group-1"

        if group_id not in rules_by_group:
            rules_by_group[group_id] = []
            match_by_group[group_id] = "all"
            group_order.append(group_id)

        rules_by_group[group_id].append(
            {
                "field": field,
                "parameter": parameter,
                "operator": operator,
                "value": value,
            }
        )

    return [
        {
            "id": group_id,
            "match": match_by_group.get(group_id, "all"),
            "rules": rules_by_group.get(group_id, []),
        }
        for group_id in group_order
        if rules_by_group.get(group_id)
    ]


def _filter_groups_from_config(config, prefix):
    """Return grouped rules, upgrading legacy flat configuration in memory."""

    groups = config.get("{}_groups".format(prefix))
    if isinstance(groups, list):
        result = []
        for index, group in enumerate(groups, start=1):
            if not isinstance(group, dict):
                continue
            rules = group.get("rules")
            if not isinstance(rules, list):
                rules = []
            result.append(
                {
                    "id": "{}-group-{}".format(prefix, index),
                    "match": str(group.get("match") or "all"),
                    "rules": rules,
                }
            )
        return result

    legacy = config.get(prefix, [])
    if not isinstance(legacy, list) or not legacy:
        return []

    return [
        {
            "id": "{}-group-1".format(prefix),
            "match": "all" if prefix == "include" else "any",
            "rules": legacy,
        }
    ]


def _normalise_inventory_ids(values):
    """
    Convert submitted or stored inventory IDs into a unique list.
    """

    inventory_ids = []
    seen_inventory_ids = set()

    for value in values or []:
        try:
            inventory_id = int(
                str(value).strip()
            )

        except (TypeError, ValueError):
            continue

        if inventory_id < 1:
            continue

        if inventory_id in seen_inventory_ids:
            continue

        seen_inventory_ids.add(
            inventory_id
        )

        inventory_ids.append(
            inventory_id
        )

    return inventory_ids


def _composite_source_ids_from_request():
    """
    Read selected Composite Inventory sources.
    """

    return _normalise_inventory_ids(
        request.form.getlist(
            "composite_source_inventory_ids"
        )
    )


def _composite_source_ids_from_config(config):
    """
    Read Composite Inventory sources from stored configuration.
    """

    values = config.get(
        "source_inventory_ids",
        [],
    )

    if not isinstance(values, list):
        return []

    return _normalise_inventory_ids(
        values
    )

def _inventory_form_data(inventory=None):
    """
    Return inventory form values for create or edit.
    """

    config = (
        inventory_config(inventory)
        if inventory is not None
        else {}
    )

    if inventory is None:
        return {
            "name": "",
            "inventory_type": "satellite",
            "credential_id": None,
            "verify_tls": True,
            "enabled": True,
            "organization": "",
            "static_content": "",
            "source_inventory_id": None,
            "include_groups": [],
            "exclude_groups": [],
            "composite_source_inventory_ids": [],
            "composite_normalize_hostnames": "none",
            "append_domain_enabled": False,
            "append_domain": "",
            "endpoint": "",
            "zabbix_tag_name": "",
            "zabbix_tag_value": "journeyman",
            "zabbix_include_disabled": False,
            "netbox_status": "active",
            "netbox_tag": "",
            "netbox_site": "",
            "netbox_role": "",
            "netbox_interfaces": True,
            "netbox_services": True,
            "netbox_config_context": True,
            "netbox_site_data": True,
            "netbox_virtual_disks": True,
            "lightspeed_tags": "",
            "ovirt_query_filter": "",
            "ovirt_hostname_preference": "fqdn, name",
            "proxy_credential_id": None,
        }

    include_groups = _filter_groups_from_config(
        config,
        "include",
    )
    exclude_groups = _filter_groups_from_config(
        config,
        "exclude",
    )

    composite_source_inventory_ids = (
        _composite_source_ids_from_config(
            config
        )
    )

    return {
        "name": inventory.name,
        "inventory_type": inventory.inventory_type,
        "credential_id": inventory.credential_id,
        "verify_tls": inventory.verify_tls,
        "enabled": inventory.enabled,
        "organization": config.get(
            "organization",
            "",
        ),
        "static_content": config.get(
            "content",
            "",
        ),
        "source_inventory_id": config.get(
            "source_inventory_id"
        ),
        "include_groups": include_groups,
        "exclude_groups": exclude_groups,
        "composite_source_inventory_ids": (
            composite_source_inventory_ids
        ),
        "composite_normalize_hostnames": config.get(
            "normalize_hostnames",
            "none",
        ),
        "append_domain_enabled": bool(config.get("append_domain", "")),
        "append_domain": config.get("append_domain", ""),
        "endpoint": inventory.endpoint or "",
        "zabbix_tag_name": config.get(
            "tag_name",
            "",
        ),
        "zabbix_tag_value": config.get(
            "tag_value",
            "journeyman",
        ),
        "zabbix_include_disabled": bool(
            config.get(
                "include_disabled",
                False,
            )
        ),
        "netbox_status": config.get("status", "active"),
        "netbox_tag": config.get("tag", ""),
        "netbox_site": config.get("site", ""),
        "netbox_role": config.get("role", ""),
        "netbox_interfaces": bool(config.get("interfaces", True)),
        "netbox_services": bool(config.get("services", True)),
        "netbox_config_context": bool(config.get("config_context", True)),
        "netbox_site_data": bool(config.get("site_data", True)),
        "netbox_virtual_disks": bool(config.get("virtual_disks", True)),
        "lightspeed_tags": config.get("tags", ""),
        "ovirt_query_filter": yaml.safe_dump(config.get("query_filter") or {}, default_flow_style=False, sort_keys=False).strip() if config.get("query_filter") else "",
        "ovirt_hostname_preference": ", ".join(config.get("hostname_preference") or ["fqdn", "name"]),
        "proxy_credential_id": config.get("proxy_credential_id"),
    }


def _inventory_form_from_request():
    """
    Read and normalize inventory configuration from the form.
    """

    inventory_type = (
        _clean(
            request.form.get(
                "inventory_type"
            )
        )
        or "satellite"
    )

    credential_id = None

    credential_field = {
        "zabbix": "zabbix_credential_id",
        "netbox": "netbox_credential_id",
        "lightspeed": "lightspeed_credential_id",
        "ovirt": "ovirt_credential_id",
    }.get(inventory_type, "credential_id")

    credential_value = _clean(
        request.form.get(
            credential_field
        )
    )

    if credential_value:
        try:
            credential_id = int(
                credential_value
            )

        except (
            TypeError,
            ValueError,
        ):
            credential_id = None

    proxy_credential_id = None
    proxy_credential_value = _clean(request.form.get("proxy_credential_id"))
    if proxy_credential_value:
        try:
            proxy_credential_id = int(proxy_credential_value)
        except (TypeError, ValueError):
            proxy_credential_id = None

    source_inventory_id = None

    source_inventory_value = _clean(
        request.form.get(
            "source_inventory_id"
        )
    )

    if source_inventory_value:
        try:
            source_inventory_id = int(
                source_inventory_value
            )

        except (
            TypeError,
            ValueError,
        ):
            source_inventory_id = None

    verify_tls_field = {
        "zabbix": "zabbix_verify_tls",
        "netbox": "netbox_verify_tls",
        "lightspeed": "lightspeed_verify_tls",
        "ovirt": "ovirt_verify_tls",
    }.get(inventory_type, "verify_tls")

    return {
        "name": _clean(
            request.form.get("name")
        ),
        "inventory_type": inventory_type,
        "credential_id": credential_id,
        "verify_tls": (
            True
            if (
                inventory_type in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}
                and current_app.config.get("OUTBOUND_SECURE_TRANSPORT_ENFORCED", False)
            )
            else request.form.get(verify_tls_field) == "on"
        ),
        "enabled": (
            request.form.get("enabled")
            == "on"
        ),
        "organization": _clean(
            request.form.get("organization")
        ),
        "static_content": (
            request.form.get(
                "static_content"
            )
            or ""
        ).strip(),
        "source_inventory_id": (
            source_inventory_id
        ),
        "include_groups": (
            _filter_groups_from_request(
                "include"
            )
        ),
        "exclude_groups": (
            _filter_groups_from_request(
                "exclude"
            )
        ),
        "composite_source_inventory_ids": (
            _composite_source_ids_from_request()
        ),
        "composite_normalize_hostnames": (
            _clean(request.form.get("composite_normalize_hostnames"))
            or "none"
        ),
        "append_domain_enabled": bool(request.form.get("append_domain_enabled")),
        "append_domain": _clean(request.form.get("append_domain")),
        "endpoint": _clean(
            request.form.get(
                "zabbix_endpoint"
            )
        ).rstrip("/"),
        "zabbix_tag_name": _clean(
            request.form.get(
                "zabbix_tag_name"
            )
        ),
        "zabbix_tag_value": _clean(
            request.form.get(
                "zabbix_tag_value"
            )
        ),
        "zabbix_include_disabled": (
            request.form.get(
                "zabbix_include_disabled"
            )
            == "on"
        ),
        "netbox_status": _clean(request.form.get("netbox_status")) or "active",
        "netbox_tag": _clean(request.form.get("netbox_tag")),
        "netbox_site": _clean(request.form.get("netbox_site")),
        "netbox_role": _clean(request.form.get("netbox_role")),
        "netbox_interfaces": request.form.get("netbox_interfaces") == "on",
        "netbox_services": request.form.get("netbox_services") == "on",
        "netbox_config_context": request.form.get("netbox_config_context") == "on",
        "netbox_site_data": request.form.get("netbox_site_data") == "on",
        "netbox_virtual_disks": request.form.get("netbox_virtual_disks") == "on",
        "lightspeed_tags": _clean(request.form.get("lightspeed_tags")),
        "ovirt_query_filter": (request.form.get("ovirt_query_filter") or "").strip(),
        "ovirt_hostname_preference": _clean(request.form.get("ovirt_hostname_preference")) or "fqdn, name",
        "proxy_credential_id": proxy_credential_id,
    }

def _filter_dependency_contains(
    source_inventory,
    target_inventory_id,
):
    """
    Return whether a filtered dependency chain reaches target.
    """

    visited = set()
    current = source_inventory

    while current is not None:
        if current.id == target_inventory_id:
            return True

        if current.id in visited:
            return True

        visited.add(
            current.id
        )

        if current.inventory_type != "filtered":
            return False

        try:
            config = inventory_config(
                current
            )

            source_inventory_id = int(
                config.get(
                    "source_inventory_id"
                )
            )

        except (
            InventoryResolutionError,
            TypeError,
            ValueError,
        ):
            return False

        current = db.session.get(
            Inventory,
            source_inventory_id,
        )

    return False


def _validate_filter_groups(groups, *, label):
    """Validate include or exclude rule groups."""

    errors = []

    if not isinstance(groups, list):
        return ["{} groups must be a list.".format(label)]

    for group_index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            errors.append(
                "{} group {} is invalid.".format(label, group_index)
            )
            continue

        match = str(group.get("match") or "").strip().lower()
        if match not in {"all", "any"}:
            errors.append(
                "{} group {} must match ALL or ANY rules.".format(
                    label, group_index
                )
            )

        rules = group.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append(
                "{} group {} must contain at least one rule.".format(
                    label, group_index
                )
            )
            continue

        for rule_index, rule in enumerate(rules, start=1):
            field = rule.get("field")
            operator = rule.get("operator")
            parameter = str(rule.get("parameter") or "").strip()
            value = str(rule.get("value") or "").strip()
            prefix = "{} group {} rule {}".format(
                label, group_index, rule_index
            )

            if field not in FILTER_FIELDS:
                errors.append("{} has an invalid field.".format(prefix))

            if operator not in FILTER_OPERATORS:
                errors.append("{} has an invalid operator.".format(prefix))

            if field == "foreman_param" and not parameter:
                errors.append(
                    "{} requires a Satellite host parameter name.".format(
                        prefix
                    )
                )

            if field == "variable" and not parameter:
                errors.append(
                    "{} requires a host variable path.".format(prefix)
                )

            if operator not in {"exists", "not_exists"} and not value:
                errors.append("{} requires a value.".format(prefix))

    return errors


def _validate_filtered_inventory_form(
    form_data,
    *,
    inventory=None,
):
    """
    Validate a filtered inventory definition.
    """

    errors = []

    source_inventory_id = form_data[
        "source_inventory_id"
    ]

    if source_inventory_id is None:
        return [
            "A source inventory is required."
        ]

    source_inventory = db.session.get(
        Inventory,
        source_inventory_id,
    )

    if source_inventory is None:
        return [
            "The selected source inventory does not exist."
        ]

    if (
        inventory is not None
        and source_inventory.id == inventory.id
    ):
        errors.append(
            "An inventory cannot use itself as its source."
        )

    if not source_inventory.enabled:
        errors.append(
            'Source inventory "{}" is disabled.'
            .format(source_inventory.name)
        )

    if source_inventory.inventory_type not in {
        "satellite",
        "static",
        "filtered",
        "composite",
        "zabbix",
        "netbox",
        "lightspeed",
        "ovirt",
    }:
        errors.append(
            "The selected source inventory type "
            "is not currently supported."
        )

    try:
        validate_inventory_dependency_update(
            (
                inventory.id
                if inventory is not None
                else None
            ),
            [
                source_inventory.id,
            ],
        )

    except InventoryDependencyError as exc:
        errors.append(
            str(exc)
        )

    errors.extend(
        _validate_filter_groups(
            form_data["include_groups"],
            label="Include",
        )
    )

    errors.extend(
        _validate_filter_groups(
            form_data["exclude_groups"],
            label="Exclude",
        )
    )

    return errors

def _validate_composite_inventory_form(
    form_data,
    *,
    inventory=None,
):
    """
    Validate a Composite Inventory definition.
    """

    errors = []

    normalize_hostnames = form_data.get(
        "composite_normalize_hostnames",
        "none",
    )
    if normalize_hostnames not in {"none", "short", "fqdn"}:
        errors.append("A valid hostname normalization mode is required.")

    source_inventory_ids = (
        form_data.get(
            "composite_source_inventory_ids",
            [],
        )
    )

    source_inventory_ids = (
        _normalise_inventory_ids(
            source_inventory_ids
        )
    )

    form_data[
        "composite_source_inventory_ids"
    ] = source_inventory_ids

    if len(source_inventory_ids) < 2:
        return [
            "A Composite Inventory requires at least "
            "two source inventories."
        ]

    source_inventories = []

    for source_inventory_id in (
        source_inventory_ids
    ):
        source_inventory = db.session.get(
            Inventory,
            source_inventory_id,
        )

        if source_inventory is None:
            errors.append(
                "One or more selected source inventories "
                "no longer exist."
            )

            continue

        source_inventories.append(
            source_inventory
        )

        if not source_inventory.enabled:
            errors.append(
                'Source inventory "{}" is disabled.'
                .format(
                    source_inventory.name
                )
            )

        if source_inventory.inventory_type not in {
            "satellite",
            "static",
            "filtered",
            "composite",
            "zabbix",
            "netbox",
            "lightspeed",
            "ovirt",
        }:
            errors.append(
                'Source inventory "{}" has an unsupported '
                "inventory type.".format(
                    source_inventory.name
                )
            )

    if errors:
        return errors

    try:
        validated_source_ids = (
            validate_inventory_dependency_update(
                (
                    inventory.id
                    if inventory is not None
                    else None
                ),
                source_inventory_ids,
            )
        )

        validate_composite_source_lineages(
            validated_source_ids,
        )

        form_data[
            "composite_source_inventory_ids"
        ] = validated_source_ids

    except InventoryDependencyError as exc:
        errors.append(
            str(exc)
        )

    return errors

def _validate_inventory_form(
    form_data,
    *,
    inventory=None,
):
    """
    Validate generic and inventory-type-specific fields.
    """

    errors = []

    if not form_data["name"]:
        errors.append(
            "Name is required."
        )
    else:
        reserved_error = reserved_name_validation_error(
            form_data["name"],
            existing_name=(
                inventory.name if inventory is not None else None
            ),
        )
        if reserved_error:
            errors.append(reserved_error)

    if form_data.get("append_domain_enabled"):
        try:
            form_data["append_domain"] = normalise_default_domain(
                form_data.get("append_domain")
            )
            if not form_data["append_domain"]:
                errors.append(
                    "A default domain is required when append domain is enabled."
                )
        except CompositeInventoryError as exc:
            errors.append(str(exc))
    else:
        form_data["append_domain"] = ""

    inventory_type = form_data[
        "inventory_type"
    ]

    if inventory_type == "satellite":
        errors.extend(
            _validate_satellite_inventory_form(
                form_data
            )
        )

    elif inventory_type == "static":
        errors.extend(
            _validate_static_inventory_form(
                form_data
            )
        )

    elif inventory_type == "filtered":
        errors.extend(
            _validate_filtered_inventory_form(
                form_data,
                inventory=inventory,
            )
        )

    elif inventory_type == "composite":
        errors.extend(
            _validate_composite_inventory_form(
                form_data,
                inventory=inventory,
            )
        )

    elif inventory_type == "zabbix":
        errors.extend(_validate_zabbix_inventory_form(form_data))

    elif inventory_type in {"netbox", "lightspeed", "ovirt"}:
        credential_id = form_data.get("credential_id")
        credential = db.session.get(Credential, credential_id) if credential_id else None
        if credential is None or credential.credential_type != CREDENTIAL_TYPE_URL:
            errors.append("A URL / API credential is required for this inventory type.")
        elif inventory_type == "netbox":
            try:
                _u, _d = url_credential_details(credential)
                if _d.get("auth_mode") != "token":
                    errors.append("NetBox requires a URL / API credential using Token authentication.")
            except URLCredentialError as exc:
                errors.append(str(exc))
        elif inventory_type == "ovirt":
            try:
                _u, _d = url_credential_details(credential)
                if _d.get("auth_mode") != "basic":
                    errors.append("oVirt / RHV requires a URL / API credential using Basic authentication.")
            except URLCredentialError as exc:
                errors.append(str(exc))
            query_text = form_data.get("ovirt_query_filter") or ""
            if query_text:
                try:
                    query_filter = yaml.safe_load(query_text)
                    if not isinstance(query_filter, dict):
                        errors.append("oVirt / RHV query filter must be a YAML mapping.")
                    else:
                        form_data["ovirt_query_filter_parsed"] = query_filter
                except yaml.YAMLError as exc:
                    errors.append("oVirt / RHV query filter is invalid YAML: {}".format(exc))

    else:
        errors.append(
            "A valid inventory type is required."
        )

    proxy_credential_id = form_data.get("proxy_credential_id")
    if proxy_credential_id is not None:
        if inventory_type not in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}:
            errors.append("Outbound proxy is only valid for URL-backed inventory types.")
        else:
            proxy_credential = db.session.get(Credential, proxy_credential_id)
            if proxy_credential is None or proxy_credential.credential_type != CREDENTIAL_TYPE_URL:
                errors.append("Proxy credential must be a URL / API credential.")
            else:
                try:
                    from app.services.url_credentials import proxy_url_for_credential
                    proxy_url_for_credential(proxy_credential)
                except Exception as exc:
                    errors.append(str(exc))

    return errors

def _validate_static_inventory_form(form_data):
    """
    Validate a static Ansible YAML inventory.
    """

    errors = []

    content = form_data["static_content"]

    if not content:
        return [
            "Static inventory YAML is required."
        ]

    try:
        parsed = yaml.safe_load(content)

    except yaml.YAMLError as exc:
        return [
            "Static inventory YAML is invalid: {}".format(
                exc
            )
        ]

    if not isinstance(parsed, dict):
        errors.append(
            "Static inventory YAML must contain "
            "an inventory mapping."
        )

    return errors

def _inventory_config_from_form(form_data):
    """
    Build provider configuration for storage in config_json.
    """

    inventory_type = form_data["inventory_type"]

    if inventory_type == "satellite":
        config = {
            "organization": form_data["organization"],
        }
    elif inventory_type == "static":
        config = {
            "content": form_data["static_content"],
        }
    elif inventory_type == "filtered":
        config = {
            "source_inventory_id": (
                form_data[
                    "source_inventory_id"
                ]
            ),
            "include_groups": [
                {
                    "match": group["match"],
                    "rules": group["rules"],
                }
                for group in form_data["include_groups"]
            ],
            "exclude_groups": [
                {
                    "match": group["match"],
                    "rules": group["rules"],
                }
                for group in form_data["exclude_groups"]
            ],
        }
    elif inventory_type == "composite":
        config = {
            "source_inventory_ids": (
                form_data[
                    "composite_source_inventory_ids"
                ]
            ),
            "normalize_hostnames": form_data.get(
                "composite_normalize_hostnames",
                "none",
            ),
        }
    elif inventory_type == "zabbix":
        config = {
            "tag_name": form_data["zabbix_tag_name"],
            "tag_value": form_data["zabbix_tag_value"],
            "include_disabled": form_data["zabbix_include_disabled"],
        }
    elif inventory_type == "netbox":
        config = {
            "status": form_data["netbox_status"],
            "tag": form_data["netbox_tag"],
            "site": form_data["netbox_site"],
            "role": form_data["netbox_role"],
            "interfaces": form_data["netbox_interfaces"],
            "services": form_data["netbox_services"],
            "config_context": form_data["netbox_config_context"],
            "site_data": form_data["netbox_site_data"],
            "virtual_disks": form_data["netbox_virtual_disks"],
        }
    elif inventory_type == "lightspeed":
        config = {
            "tags": form_data["lightspeed_tags"],
        }
    elif inventory_type == "ovirt":
        config = {
            "query_filter": form_data.get("ovirt_query_filter_parsed") or None,
            "hostname_preference": [
                part.strip() for part in form_data.get("ovirt_hostname_preference", "fqdn, name").split(",")
                if part.strip()
            ] or ["fqdn", "name"],
        }
    else:
        return {}

    if inventory_type in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}:
        proxy_credential_id = form_data.get("proxy_credential_id")
        if proxy_credential_id is not None:
            config["proxy_credential_id"] = proxy_credential_id

    append_domain = form_data.get("append_domain", "")
    if append_domain:
        config["append_domain"] = append_domain

    return config

def _validate_zabbix_inventory_form(form_data):
    """Validate Zabbix inventory configuration with URL credentials preferred."""
    errors = []
    credential_id = form_data["credential_id"]
    credential = db.session.get(Credential, credential_id) if credential_id else None
    if credential is None or credential.credential_type not in {CREDENTIAL_TYPE_ZABBIX, CREDENTIAL_TYPE_URL}:
        errors.append("A URL / API or legacy Zabbix credential is required.")
    elif credential.credential_type == CREDENTIAL_TYPE_ZABBIX:
        endpoint = form_data["endpoint"]
        if not endpoint:
            errors.append("Legacy Zabbix credentials require a Zabbix API URL.")
        else:
            try:
                form_data["endpoint"] = validate_outbound_url(endpoint, purpose="Zabbix API")
            except OutboundSecurityError as exc:
                errors.append(str(exc))
    if not form_data["zabbix_tag_name"]:
        errors.append("Zabbix host tag name is required.")
    if not form_data["zabbix_tag_value"]:
        errors.append("Zabbix host tag value is required.")
    return errors


def _validate_satellite_inventory_form(form_data):
    """Validate Satellite inventory configuration; URL credentials are preferred."""
    errors = []
    if not form_data["organization"]:
        errors.append("Satellite organization is required.")
    credential_id = form_data["credential_id"]
    credential = db.session.get(Credential, credential_id) if credential_id else None
    if credential is None or credential.credential_type not in {CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_URL}:
        errors.append("A URL / API or legacy Red Hat Satellite credential is required.")
    return errors


def _repository_scripts(repository):
    """Return repository files suitable for Script execution."""
    repository_root = Path(safe_repository_dir(
        current_app.config["REPOSITORY_ROOT"], repository.id
    ))
    if not repository_root.is_dir():
        return []
    results = []
    for path in repository_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(repository_root).as_posix()
        if relative_path.startswith(".git/"):
            continue
        try:
            with path.open("rb") as handle:
                first_line = handle.readline(256)
        except OSError:
            continue
        if path.suffix.lower() == ".sh" or first_line.startswith(b"#!"):
            results.append({"path": relative_path, "classification": "shell_script"})
    return sorted(results, key=lambda entry: entry["path"].lower())

def _project_steps_from_request():
    """
    Read the ordered ProjectStep rows submitted by project_form.html.
    """

    names = request.form.getlist(
        "step_name"
    )

    repository_ids = request.form.getlist(
        "step_repository_id"
    )

    inventory_ids = request.form.getlist(
        "step_inventory_id"
    )

    environment_ids = request.form.getlist(
        "step_environment_id"
    )

    playbooks = request.form.getlist(
        "step_playbook"
    )

    limits = request.form.getlist(
        "step_limit"
    )

    tags = request.form.getlist(
        "step_tags"
    )

    skip_tags = request.form.getlist(
        "step_skip_tags"
    )

    extra_vars_values = request.form.getlist(
        "step_extra_vars"
    )

    verbosities = request.form.getlist(
        "step_verbosity"
    )

    failure_behaviours = request.form.getlist(
        "step_failure_behaviour"
    )

    failure_only_indexes = {
        int(value)
        for value in request.form.getlist("step_failure_only")
        if str(value).isdigit()
    }

    remote_shell_serials = request.form.getlist(
        "step_remote_shell_serial"
    )

    remote_shell_become_indexes = {
        int(value)
        for value in request.form.getlist("step_remote_shell_become")
        if str(value).isdigit()
    }

    refresh_repository_indexes = {
        int(value)
        for value in request.form.getlist("step_refresh_repository")
        if str(value).isdigit()
    }

    refresh_inventory_after_indexes = {
        int(value)
        for value in request.form.getlist("step_refresh_inventory_after")
        if str(value).isdigit()
    }

    oversight_after_indexes = {
        int(value)
        for value in request.form.getlist("step_oversight_after")
        if str(value).isdigit()
    }

    credential_override_indexes = {
        int(value)
        for value in request.form.getlist("step_credentials_override")
        if str(value).isdigit()
    }

    row_count = max(
        len(names),
        len(repository_ids),
        len(inventory_ids),
        len(environment_ids),
        len(playbooks),
        len(limits),
        len(tags),
        len(skip_tags),
        len(extra_vars_values),
        len(verbosities),
        len(failure_behaviours),
        0,
    )

    rows = []

    for index in range(row_count):
        repository_id = None
        inventory_id  = None
        environment_id = None
        credential_ids = []
        verbosity = 0
        remote_shell_serial = 0
        inventory_id = None

        if index < len(repository_ids):
            try:
                repository_id = int(repository_ids[index])
            except (TypeError, ValueError):
                repository_id = None

        for value in request.form.getlist(
            f"step_{index}_credential_ids"
        ):
            try:
                credential_id = int(value)
            except (TypeError, ValueError):
                continue
            if credential_id not in credential_ids:
                credential_ids.append(credential_id)

        if index < len(verbosities):
            try:
                verbosity = int(verbosities[index])
            except (TypeError, ValueError):
                verbosity = 0

        if index < len(remote_shell_serials):
            try:
                remote_shell_serial = int(remote_shell_serials[index] or 0)
            except (TypeError, ValueError):
                remote_shell_serial = -1

        if index < len(inventory_ids):
            inventory_value = _clean(
                inventory_ids[index]
            )

            if inventory_value:
                try:
                    inventory_id = int(
                        inventory_value
                    )
                except (TypeError, ValueError):
                    # Ensure malformed submitted IDs fail validation
                    # instead of silently inheriting the project inventory.
                    inventory_id = -1

        if index < len(environment_ids):
            environment_value = _clean(environment_ids[index])
            if environment_value:
                try:
                    environment_id = int(environment_value)
                except (TypeError, ValueError):
                    environment_id = -1

        dependency_positions = []
        for value in request.form.getlist(
            f"step_{index}_dependency_positions"
        ):
            try:
                dependency_position = int(value)
            except (TypeError, ValueError):
                continue
            if dependency_position not in dependency_positions:
                dependency_positions.append(dependency_position)

        rows.append(
            {
                "name": (
                    _clean(names[index])
                    if index < len(names)
                    else ""
                ),
                "repository_id": repository_id,
                "environment_id": environment_id,
                "credential_ids": credential_ids,
                "playbook": (
                    _clean(playbooks[index])
                    if index < len(playbooks)
                    else ""
                ),
                "limit": (
                    _clean(limits[index])
                    if index < len(limits)
                    else ""
                ),
                "tags": (
                    _clean(tags[index])
                    if index < len(tags)
                    else ""
                ),
                "skip_tags": (
                    _clean(skip_tags[index])
                    if index < len(skip_tags)
                    else ""
                ),
                "extra_vars_yaml": (
                    str(extra_vars_values[index] or "").strip()
                    if index < len(extra_vars_values)
                    else ""
                ),
                "extra_vars": {},
                "verbosity": verbosity,
                "check_mode": False,
                "remote_shell_become": index in remote_shell_become_indexes,
                "remote_shell_serial": remote_shell_serial,
                "continue_on_failure": (
                    index < len(failure_behaviours)
                    and failure_behaviours[index] == "continue"
                ),
                "failure_only": index in failure_only_indexes,
                "refresh_repository": index in refresh_repository_indexes,
                "refresh_inventory_after": (
                    index in refresh_inventory_after_indexes
                ),
                "oversight_after": index in oversight_after_indexes,
                "credentials_override": (
                    index in credential_override_indexes
                    or bool(credential_ids)
                ),
                "dependency_positions": sorted(dependency_positions),
                "enabled": True,
                "inventory_id": inventory_id,
            }
        )

    return rows


def _project_steps_for_form(project):
    """
    Convert existing ProjectStep objects into form rows.
    """

    return [
        {
            "name": step.name,
            "repository_id": step.repository_id,
            "environment_id": step.environment_id,
            "credential_ids": [
                credential.id
                for credential in step.credentials
            ],
            "playbook": step.playbook,
            "limit": step.limit,
            "tags": step.tags,
            "skip_tags": step.skip_tags,
            "extra_vars_yaml": (
                yaml.safe_dump(
                    step.get_extra_vars(),
                    default_flow_style=False,
                    sort_keys=True,
                ).strip()
                if step.get_extra_vars()
                else ""
            ),
            "extra_vars": step.get_extra_vars(),
            "verbosity": step.verbosity,
            "check_mode": step.check_mode,
            "remote_shell_become": step.remote_shell_become,
            "remote_shell_serial": step.remote_shell_serial,
            "continue_on_failure": step.continue_on_failure,
            "failure_only": step.failure_only,
            "refresh_repository": step.refresh_repository,
            "refresh_inventory_after": step.refresh_inventory_after,
            "oversight_after": step.oversight_after,
            "credentials_override": step.credentials_override,
            "dependency_positions": step.get_dependency_positions(),
            "enabled": step.enabled,
            "inventory_id": step.inventory_id,
        }
        for step in project.steps
    ]


def _validate_project_steps(
    step_rows,
    playbooks_by_repository,
    available_credential_ids,
    *,
    default_repository_id=None,
    default_environment_id=None,
    default_credential_ids=None,
    execution_type="ansible",
    dispatch_validation=True,
):
    """
    Validate submitted workflow steps.

    Save-time validation is intentionally limited to values that must be
    parseable and safely persistable. Dispatch-time validation additionally
    checks whether the workflow is executable. This allows administrators to
    save partially configured Projects while they are being developed.
    """

    errors = []
    default_credential_ids = list(default_credential_ids or [])

    if not step_rows:
        return ["At least one workflow step is required."]

    step_count = len(step_rows)
    dependency_map = {
        position: row.get("dependency_positions", [])
        for position, row in enumerate(step_rows, start=1)
    }

    if dispatch_validation:
        for position, dependencies in dependency_map.items():
            for dependency in dependencies:
                if dependency < 1 or dependency > step_count:
                    errors.append(
                        f"Step {position} references a dependency that does not exist."
                    )
                elif dependency == position:
                    errors.append(f"Step {position} cannot depend on itself.")

        visiting = set()
        visited = set()

        def visit(position):
            if position in visiting:
                return True
            if position in visited:
                return False
            visiting.add(position)
            for dependency in dependency_map.get(position, []):
                if dependency in dependency_map and visit(dependency):
                    return True
            visiting.remove(position)
            visited.add(position)
            return False

        if any(visit(position) for position in dependency_map):
            errors.append("Workflow step dependencies contain a cycle.")

    for position, row in enumerate(step_rows, start=1):
        if (
            dispatch_validation
            and row.get("failure_only")
            and not row.get("dependency_positions")
        ):
            errors.append(
                f"Step {position} is failure-only and must depend on "
                "at least one other step."
            )

        if dispatch_validation:
            repository_id = row.get("repository_id") or default_repository_id
            repository = (
                db.session.get(Repository, repository_id)
                if repository_id is not None
                else None
            )

            if repository is None:
                errors.append(
                    f"Step {position} requires a valid repository."
                )
                available_paths = set()
            else:
                if repository.status != "up_to_date":
                    errors.append(
                        f'Step {position} repository "{repository.name}" '
                        "must be synchronized first."
                    )

                available_paths = {
                    entry["path"]
                    for entry in playbooks_by_repository.get(
                        repository.id,
                        [],
                    )
                }

            inventory_id = row.get(
                "inventory_id"
            )

            if inventory_id is not None:
                inventory = db.session.get(
                    Inventory,
                    inventory_id,
                )

                if inventory is None:
                    errors.append(
                        f"Step {position} references an inventory "
                        "that does not exist."
                    )

                elif not inventory.enabled:
                    errors.append(
                        f'Step {position} inventory '
                        f'"{inventory.name}" is disabled.'
                    )

            environment_id = row.get("environment_id") or default_environment_id
            if environment_id is not None:
                environment = db.session.get(Environment, environment_id)
                if environment is None:
                    errors.append(f"Step {position} references an environment that does not exist.")
                elif not environment.enabled:
                    errors.append(f'Step {position} environment "{environment.name}" is disabled.')
                elif environment.validation_status != "passed":
                    errors.append(f'Step {position} environment "{environment.name}" has not passed validation.')

        raw_step_extra_vars = str(row.get("extra_vars_yaml") or "").strip()
        step_extra_vars = {}
        if raw_step_extra_vars:
            try:
                parsed_step_extra_vars = yaml.safe_load(raw_step_extra_vars)
            except yaml.YAMLError as exc:
                errors.append(
                    f"Step {position} extra variables are not valid YAML: {exc}."
                )
            else:
                if parsed_step_extra_vars is None:
                    parsed_step_extra_vars = {}
                if not isinstance(parsed_step_extra_vars, dict):
                    errors.append(
                        f"Step {position} extra variables must be a YAML mapping."
                    )
                elif "journeyman_stats" in parsed_step_extra_vars:
                    errors.append(
                        f"Step {position} extra variable 'journeyman_stats' is reserved by Journeyman."
                    )
                else:
                    invalid_names = [
                        str(name)
                        for name in parsed_step_extra_vars
                        if (
                            not isinstance(name, str)
                            or not VARIABLE_NAME_PATTERN.fullmatch(name)
                        )
                    ]
                    if invalid_names:
                        errors.append(
                            f"Step {position} extra variable names must be valid Ansible variable names: "
                            + ", ".join(invalid_names)
                            + "."
                        )
                    else:
                        try:
                            json.dumps(parsed_step_extra_vars)
                        except (TypeError, ValueError) as exc:
                            errors.append(
                                f"Step {position} extra variables must contain JSON-safe values: {exc}."
                            )
                        else:
                            step_extra_vars = parsed_step_extra_vars
        row["extra_vars"] = step_extra_vars

        verbosity = row.get("verbosity", 0)

        if verbosity < 0 or verbosity > 5:
            errors.append(
                f"Step {position} verbosity must be from 0 to 5."
            )

        if dispatch_validation:
            credential_ids = (
                row.get("credential_ids", [])
                if row.get("credentials_override")
                else default_credential_ids
            )

            selected_credentials = []
            for credential_id in credential_ids:
                if credential_id not in available_credential_ids:
                    errors.append(
                        f"Step {position} references a credential "
                        "that does not exist or cannot be used."
                    )
                    continue

                credential = db.session.get(Credential, credential_id)
                if credential is not None:
                    selected_credentials.append(credential)

            credential_types = {}
            for credential in selected_credentials:
                credential_types.setdefault(
                    credential.credential_type,
                    [],
                ).append(credential.name)

            for credential_type, names in credential_types.items():
                if len(names) > 1:
                    errors.append(
                        f"Step {position} cannot use more than one "
                        f"{credential_type!r} credential: "
                        + ", ".join(names)
                        + "."
                    )

        if dispatch_validation:
            playbook = row.get("playbook", "")
            artifact_label = (
                "script" if execution_type in {"shell", "remote_shell"}
                else "Ansible YAML file"
            )

            if not playbook:
                article = "an" if artifact_label == "Ansible YAML file" else "a"
                errors.append(f"Step {position} requires {article} {artifact_label}.")
            elif repository is not None and playbook not in available_paths:
                errors.append(
                    f"Step {position} references a {artifact_label} that "
                    "does not exist in its repository."
                )

    return errors

@bp.get("/")
def index():
    """Render the role-aware Journeyman dashboard."""

    is_admin = current_user_is_admin()

    jobs_query = Job.query
    if not is_admin:
        jobs_query = jobs_query.filter(
            Job.requested_by == current_username()
        )

    active_statuses = ("queued", "running", "waiting_oversight", "cancelling")
    active_job_count = (
        jobs_query
        .filter(Job.status.in_(active_statuses))
        .count()
    )
    failed_job_count = (
        jobs_query
        .filter(Job.status == "failed")
        .count()
    )
    recent_jobs = (
        jobs_query
        .order_by(Job.id.desc())
        .limit(8)
        .all()
    )

    all_packages = (
        ProjectPackage.query
        .order_by(ProjectPackage.name.asc())
        .all()
    )
    available_packages = [
        package
        for package in all_packages
        if can_launch_package(package)
    ]

    dashboard = {
        "active_job_count": active_job_count,
        "failed_job_count": failed_job_count,
        "available_package_count": len(available_packages),
        "recent_jobs": recent_jobs,
        "available_packages": available_packages[:6],
    }

    if is_admin:
        dashboard.update(
            {
                "enabled_project_count": Project.query.filter_by(
                    enabled=True
                ).count(),
                "enabled_package_count": ProjectPackage.query.filter_by(
                    enabled=True
                ).count(),
                "repository_problem_count": Repository.query.filter(
                    Repository.status.in_(("failed", "never_synced"))
                ).count(),
                "inventory_problem_count": Inventory.query.filter(
                    Inventory.enabled.is_(True),
                    Inventory.inventory_type != "static",
                    Inventory.status.in_(("failed", "never_synced")),
                ).count(),
                "storage": collect_storage_status(),
            }
        )

    return render_template(
        "dashboard.html",
        dashboard=dashboard,
        is_admin=is_admin,
    )


def _dashboard_live_fingerprint(is_admin):
    """Return a compact fingerprint for Dashboard-visible state."""

    jobs_query = Job.query
    if not is_admin:
        jobs_query = jobs_query.filter(
            Job.requested_by == current_username()
        )

    payload = {
        "jobs": [
            {
                "id": job.id,
                "status": job.status,
                "message": job.message,
                "started_at": (
                    job.started_at.isoformat()
                    if job.started_at else None
                ),
                "finished_at": (
                    job.finished_at.isoformat()
                    if job.finished_at else None
                ),
            }
            for job in (
                jobs_query.order_by(Job.id.desc()).limit(8).all()
            )
        ],
        "active_job_count": jobs_query.filter(
            Job.status.in_(("queued", "running", "waiting_oversight", "cancelling"))
        ).count(),
        "failed_job_count": jobs_query.filter(
            Job.status == "failed"
        ).count(),
    }

    if is_admin:
        payload.update(
            {
                "enabled_project_count": Project.query.filter_by(
                    enabled=True
                ).count(),
                "enabled_package_count": ProjectPackage.query.filter_by(
                    enabled=True
                ).count(),
                "repository_problem_count": Repository.query.filter(
                    Repository.status.in_(("failed", "never_synced"))
                ).count(),
                "inventory_problem_count": Inventory.query.filter(
                    Inventory.enabled.is_(True),
                    Inventory.inventory_type != "static",
                    Inventory.status.in_(("failed", "never_synced")),
                ).count(),
                "runner_health": [
                    [
                        runner.id,
                        runner_health(runner),
                        (
                            runner.last_heartbeat_at.isoformat()
                            if runner.last_heartbeat_at else None
                        ),
                        runner.running_steps,
                    ]
                    for runner in Runner.query.order_by(Runner.id.asc()).all()
                ],
                "storage": [
                    {
                        "path": row.get("path"),
                        "status": row.get("status"),
                        "used_percent": row.get("used_percent"),
                        "free_bytes": row.get("free_bytes"),
                        "error": row.get("error"),
                    }
                    for row in collect_storage_status()
                ],
            }
        )

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )


@bp.get("/dashboard/events")
def dashboard_events():
    is_admin = current_user_is_admin()

    @stream_with_context
    def generate():
        last_fingerprint = None
        heartbeat_counter = 0

        while True:
            db.session.expire_all()
            fingerprint = _dashboard_live_fingerprint(is_admin)

            if fingerprint != last_fingerprint:
                yield "event: dashboard-update\ndata: {}\n\n"
                last_fingerprint = fingerprint
                heartbeat_counter = 0
            else:
                heartbeat_counter += 1
                if heartbeat_counter >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

            time.sleep(1)

    response = Response(
        generate(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.route(
    "/settings/directory",
    methods=["GET", "POST"],
)
def directory_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_directory_settings()
    errors = []

    if request.method == "POST":
        form_data = directory_settings_form_data(
            request.form
        )

        try:
            validated_values = validate_directory_settings(
                form_data,
                existing_settings=settings,
            )
        except DirectorySettingsValidationError as exc:
            errors = list(exc.errors)

            return (
                render_template(
                    "directory_settings.html",
                    settings=settings,
                    form_data=form_data,
                    errors=errors,
                ),
                400,
            )

        try:
            update_directory_settings(
                settings,
                validated_values,
                updated_by=current_username(),
            )
        except CredentialCryptoError as exc:
            db.session.rollback()
            errors = [str(exc)]

            return (
                render_template(
                    "directory_settings.html",
                    settings=settings,
                    form_data=form_data,
                    errors=errors,
                ),
                500,
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Unable to save LDAP directory settings"
            )
            errors = [
                "Unable to save directory settings."
            ]

            return (
                render_template(
                    "directory_settings.html",
                    settings=settings,
                    form_data=form_data,
                    errors=errors,
                ),
                500,
            )

        record_audit_event(
            "directory.settings.update",
            object_type="directory_settings",
            object_id=settings.id,
            object_name="Directory and Authentication",
            details={
                "enabled": settings.enabled,
                "server_count": len(settings.servers),
                "administrator_group": settings.administrator_group_name,
                "user_group": settings.user_group_name,
            },
        )
        flash(
            "Directory settings saved.",
            "success",
        )

        return redirect(
            url_for("main.directory_settings")
        )

    return render_template(
        "directory_settings.html",
        settings=settings,
        form_data=directory_settings_to_form_data(
            settings
        ),
        errors=errors,
    )


@bp.post("/settings/directory/test")
def test_directory_settings():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_directory_settings()

    directory_client = get_directory_client(
        settings
    )

    try:
        results = directory_client.test_servers()
    except DirectoryError as exc:
        flash(
            "Directory test failed: {}".format(exc),
            "error",
        )
        return redirect(
            url_for("main.directory_settings")
        )

    result_by_id = {
        result["server"].id: result
        for result in results
    }

    now = _utcnow()

    for server in settings.servers:
        result = result_by_id.get(server.id)

        if result is None:
            continue

        server.last_test_ok = result["ok"]
        server.last_test_message = (
            str(result.get("message", ""))
            .replace("\x00", "")[:1000]
        )
        server.last_test_at = now

    db.session.commit()

    successful = sum(
        1 for result in results if result["ok"]
    )

    directory_validation = None
    directory_validation_error = ""

    if successful:
        try:
            directory_validation = (
                directory_client.validate_directory_configuration(
                    require_enabled=False
                )
            )
        except DirectoryError as exc:
            directory_validation_error = str(exc)

    if (
        successful == len(results)
        and directory_validation is not None
    ):
        flash(
            "All {} enabled directory servers passed. Base DN, "
            "User search base, Group search base, and both role "
            "groups validated. {} eligible user(s) were found."
            .format(
                successful,
                directory_validation["eligible_user_count"],
            ),
            "success",
        )
    else:
        message = (
            "{} of {} enabled directory servers passed."
            .format(successful, len(results))
        )

        if directory_validation_error:
            message += " Directory validation failed: {}".format(
                directory_validation_error
            )

        flash(message, "error")

    return redirect(
        url_for("main.directory_settings")
    )


def _package_grants_by_principal_guid(permissions):
    """Return Package names keyed by AD object GUID, skipping stale grants."""
    grants = {}

    for permission in permissions:
        if (
            not permission.principal_object_guid
            or permission.package is None
        ):
            continue

        grants.setdefault(
            permission.principal_object_guid.lower(),
            [],
        ).append(permission.package.name)

    for package_names in grants.values():
        package_names.sort(key=str.casefold)

    return grants


@bp.get("/users")
def users():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_directory_settings()
    directory_users = []
    directory_error = ""

    if settings.enabled:
        try:
            directory_users = get_directory_client(
                settings
            ).role_users()
        except DirectoryError as exc:
            directory_error = str(exc)

    user_package_grants = _package_grants_by_principal_guid(
        ProjectPackagePermission.query
        .filter_by(principal_type="user")
        .all()
    )

    return render_template(
        "users.html",
        directory_settings=settings,
        users=directory_users,
        user_package_grants=user_package_grants,
        directory_error=directory_error,
    )


@bp.get("/teams")
def teams():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_directory_settings()
    search_query = _clean(
        request.args.get("q")
    )
    search_results = []
    directory_error = ""

    if search_query and settings.enabled:
        try:
            search_results = get_directory_client(
                settings
            ).search_groups(search_query)
        except DirectoryError as exc:
            directory_error = str(exc)

    registered_teams = Team.query.order_by(
        Team.display_name.asc()
    ).all()

    registered_guids = {
        team.object_guid
        for team in registered_teams
    }

    team_package_grants = _package_grants_by_principal_guid(
        ProjectPackagePermission.query
        .filter_by(principal_type="group")
        .all()
    )

    selected_team = None
    team_members = []

    raw_selected_id = _clean(
        request.args.get("selected")
    )

    if raw_selected_id:
        try:
            selected_id = int(raw_selected_id)
        except ValueError:
            abort(400)

        selected_team = db.session.get(
            Team,
            selected_id,
        )

        if selected_team is None:
            abort(404)

        if settings.enabled:
            try:
                team_members = get_directory_client(
                    settings
                ).group_users(selected_team)
            except DirectoryError as exc:
                directory_error = str(exc)

    return render_template(
        "teams.html",
        directory_settings=settings,
        teams=registered_teams,
        search_query=search_query,
        search_results=search_results,
        registered_guids=registered_guids,
        team_package_grants=team_package_grants,
        selected_team=selected_team,
        team_members=team_members,
        directory_error=directory_error,
    )


@bp.post("/teams")
def add_team():
    if not current_user_is_admin():
        abort(403)

    settings = get_or_create_directory_settings()
    distinguished_name = _clean(
        request.form.get("distinguished_name")
    )

    if not distinguished_name:
        flash(
            "Select an AD group to add as a Team.",
            "error",
        )
        return redirect(url_for("main.teams"))

    try:
        group = get_directory_client(
            settings
        ).find_group_by_dn(distinguished_name)
    except DirectoryError as exc:
        flash(
            "Unable to add Team: {}".format(exc),
            "error",
        )
        return redirect(
            url_for(
                "main.teams",
                q=request.form.get("search_query", ""),
            )
        )

    existing = Team.query.filter(
        or_(
            Team.object_guid == group.object_guid,
            Team.distinguished_name == (
                group.distinguished_name
            ),
        )
    ).first()

    if existing is not None:
        flash(
            'AD group "{}" is already a Team.'
            .format(existing.display_name),
            "error",
        )
        return redirect(url_for("main.teams"))

    team = Team(
        object_guid=group.object_guid,
        distinguished_name=group.distinguished_name,
        sam_account_name=group.sam_account_name,
        display_name=group.display_name,
        description=group.description,
        created_by=current_username(),
    )

    db.session.add(team)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Unable to add AD-backed Team %s",
            group.object_guid,
        )
        flash(
            "Unable to add the selected Team.",
            "error",
        )
        return redirect(
            url_for(
                "main.teams",
                q=request.form.get("search_query", ""),
            )
        )

    record_audit_event(
        "team.create",
        object_type="team",
        object_id=team.id,
        object_name=team.display_name,
        details={"ad_object_guid": team.object_guid},
    )
    flash(
        'Team "{}" added.'.format(team.display_name),
        "success",
    )

    return redirect(url_for("main.teams"))


@bp.post("/teams/<int:team_id>/delete")
def delete_team(team_id):
    if not current_user_is_admin():
        abort(403)

    team = db.session.get(Team, team_id)

    if team is None:
        abort(404)

    package_grants = (
        ProjectPackagePermission.query
        .filter_by(
            principal_type="group",
            principal_object_guid=team.object_guid,
        )
        .count()
    )

    if package_grants:
        flash(
            'Team "{}" cannot be removed while it has {} '
            'Package execute grant{}.'
            .format(
                team.display_name,
                package_grants,
                "" if package_grants == 1 else "s",
            ),
            "error",
        )
        return redirect(url_for("main.teams"))

    team_name = team.display_name
    db.session.delete(team)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Unable to remove Team %s",
            team_id,
        )
        flash(
            'Unable to remove Team "{}".'
            .format(team_name),
            "error",
        )
        return redirect(url_for("main.teams"))

    record_audit_event(
        "team.delete",
        object_type="team",
        object_id=team_id,
        object_name=team_name,
    )
    flash(
        'Team "{}" removed. AD was not changed.'
        .format(team_name),
        "success",
    )

    return redirect(url_for("main.teams"))


# Import route modules after the main blueprint and shared legacy routes have
# been defined. Each module decorates this same blueprint, preserving the
# existing ``main.<endpoint>`` endpoint names and URLs.
from .views import jobs as _job_routes  # noqa: E402,F401
from .views import packages as _package_routes  # noqa: E402,F401
from .views import projects as _project_routes  # noqa: E402,F401
from .views import audit as _audit_routes  # noqa: E402,F401
from .views import credentials as _credential_routes  # noqa: E402,F401
from .views import environments as _environment_routes  # noqa: E402,F401
from .views import inventories as _inventory_routes  # noqa: E402,F401
from .views import repositories as _repository_routes  # noqa: E402,F401
from .views import runners as _runner_routes  # noqa: E402,F401
from .views import settings as _settings_routes  # noqa: E402,F401
from .views import schedules as _schedule_routes  # noqa: E402,F401
from .views import system_status as _system_status_routes  # noqa: E402,F401
from .views import reactions as _reaction_routes  # noqa: E402,F401


from .views import notifications as _notification_routes  # noqa: E402,F401
from .views import dispatch_progress as _dispatch_progress_routes  # noqa: E402,F401
from .views import navigation_status as _navigation_status_routes  # noqa: E402,F401
