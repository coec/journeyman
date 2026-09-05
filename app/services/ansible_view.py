"""Render Journeyman resources as supported Ansible collection invocations."""

import yaml

from app import db
from app.models import Inventory
from app.services.credential_configuration import credential_configuration_document
from app.services.package_configuration import package_configuration_document
from app.services.reactor_configuration import reactor_configuration_document
from app.services.schedule_configuration import schedule_configuration_document
from app.services.signal_source_configuration import signal_source_configuration_document


class _AnsibleViewDumper(yaml.SafeDumper):
    """YAML dumper tuned for readable Show Ansible output."""


def _represent_ansible_string(dumper, value):
    # Multiline values such as static inventory content are significantly
    # easier to read and copy when emitted as literal block scalars.  Treat
    # them as normal text files for display purposes: normalise line endings
    # and ensure a final newline so PyYAML emits ``|`` rather than an escaped
    # quoted scalar (or ``|-``).
    if "\n" in value or "\r" in value:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        if not value.endswith("\n"):
            value += "\n"
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str",
            value,
            style="|",
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_AnsibleViewDumper.add_representer(str, _represent_ansible_string)


def _dump_task(task_name, module_name, params):
    document = [
        {
            "name": task_name,
            module_name: params,
        }
    ]
    return yaml.dump(
        document,
        Dumper=_AnsibleViewDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()


def project_configuration_params(project):
    """Return a round-trippable journeyman.configuration.project payload."""

    steps = []
    for step in project.steps:
        row = {
            "name": step.name,
            "repository": step.repository.name if step.repository else "",
            "inventory": step.inventory.name if step.inventory else "",
            "environment": step.environment.name if step.environment else "",
            "playbook": step.playbook or "",
            "limit": step.limit or "",
            "tags": step.tags or "",
            "skip_tags": step.skip_tags or "",
            "extra_vars": step.get_extra_vars(),
            "verbosity": step.verbosity,
            "check_mode": bool(step.check_mode),
            "continue_on_failure": bool(step.continue_on_failure),
            "failure_only": bool(step.failure_only),
            "refresh_repository": bool(step.refresh_repository),
            "refresh_inventory_after": bool(step.refresh_inventory_after),
            "oversight_after": bool(step.oversight_after),
            "depends_on": [
                project.steps[position - 1].name
                for position in step.get_dependency_positions()
                if 1 <= position <= len(project.steps)
            ],
            "enabled": bool(step.enabled),
        }
        # Omitting credentials preserves inheritance from the Project defaults.
        # Supplying an empty list means an explicit "no credentials" override.
        if step.credentials_override:
            row["credentials"] = [credential.name for credential in step.credentials]
        steps.append(row)

    return {
        "name": project.name,
        "description": project.description or "",
        "execution_type": project.execution_type or "ansible",
        "inventory": project.inventory.name if project.inventory else "",
        "repository": project.repository.name if project.repository else "",
        "environment": project.environment.name if project.environment else "",
        "credentials": [credential.name for credential in project.credentials],
        "max_parallel_steps": project.max_parallel_steps,
        "concurrency_policy": project.concurrency_policy or "unrestricted",
        "oversight_required_between_all_steps": bool(
            project.oversight_required_between_all_steps
        ),
        "enabled": bool(project.enabled),
        "steps": steps,
        "state": "present",
    }


def package_configuration_params(package):
    """Return a journeyman.configuration.package payload."""

    params = dict(package_configuration_document(package))
    params.pop("id", None)
    params["state"] = "present"
    return params


def project_configuration_yaml(project):
    return _dump_task(
        "Configure Journeyman Project: {}".format(project.name),
        "journeyman.configuration.project",
        project_configuration_params(project),
    )


def package_configuration_yaml(package):
    return _dump_task(
        "Configure Journeyman Package: {}".format(package.name),
        "journeyman.configuration.package",
        package_configuration_params(package),
    )


def dispatch_yaml(resource_type, name):
    if resource_type not in {"project", "package"}:
        raise ValueError("Unsupported Journeyman dispatch resource type.")
    return _dump_task(
        "Dispatch Journeyman {}: {}".format(resource_type.title(), name),
        "journeyman.operation.dispatch",
        {
            "type": resource_type,
            "name": name,
        },
    )


def _configuration_yaml(resource_label, module_name, name, params):
    values = dict(params)
    values.pop("id", None)
    values.pop("status", None)
    values.pop("next_run_at", None)
    values["state"] = "present"
    return _dump_task(
        "Configure Journeyman {}: {}".format(resource_label, name),
        "journeyman.configuration.{}".format(module_name),
        values,
    )


def inventory_configuration_params(inventory):
    """Return a round-trippable journeyman.configuration.inventory payload."""
    import json

    try:
        config = json.loads(inventory.config_json or "{}")
    except (TypeError, ValueError):
        config = {}

    params = {
        "name": inventory.name,
        "inventory_type": inventory.inventory_type,
        "enabled": bool(inventory.enabled),
        "append_domain": str(config.get("append_domain") or ""),
    }
    if inventory.credential:
        params["credential"] = inventory.credential.name
    if inventory.inventory_type in {"satellite", "zabbix", "netbox", "lightspeed", "ovirt"}:
        params["verify_tls"] = bool(inventory.verify_tls)
        proxy_id = config.get("proxy_credential_id")
        if proxy_id:
            from app.models import Credential
            proxy = db.session.get(Credential, proxy_id)
            if proxy is not None:
                params["proxy_credential"] = proxy.name

    kind = inventory.inventory_type
    if kind == "static":
        params["content"] = str(config.get("content") or "")
    elif kind == "satellite":
        params["organization"] = str(config.get("organization") or "")
    elif kind == "zabbix":
        if inventory.endpoint:
            params["endpoint"] = inventory.endpoint
        params.update({
            "tag_name": str(config.get("tag_name") or ""),
            "tag_value": str(config.get("tag_value") or "journeyman"),
            "include_disabled": bool(config.get("include_disabled", False)),
        })
    elif kind == "filtered":
        source_id = config.get("source_inventory_id")
        source = db.session.get(Inventory, source_id) if source_id else None
        params.update({
            "source_inventory": source.name if source else "",
            "include_groups": config.get("include_groups", []) or [],
            "exclude_groups": config.get("exclude_groups", []) or [],
        })
    elif kind == "composite":
        names = []
        for source_id in config.get("source_inventory_ids", []) or []:
            source = db.session.get(Inventory, source_id)
            if source is not None:
                names.append(source.name)
        params["source_inventories"] = names
        params["normalize_hostnames"] = str(config.get("normalize_hostnames") or "none")
    elif kind == "netbox":
        for key, default in (
            ("status", "active"), ("tag", ""), ("site", ""), ("role", ""),
            ("interfaces", True), ("services", True), ("config_context", True),
            ("site_data", True), ("virtual_disks", True),
        ):
            params[key] = config.get(key, default)
    elif kind == "lightspeed":
        params["tags"] = str(config.get("tags") or "")
    elif kind == "ovirt":
        params["query_filter"] = config.get("query_filter") or {}
        params["hostname_preference"] = config.get("hostname_preference") or ["fqdn", "name"]

    params["state"] = "present"
    return params


def inventory_configuration_yaml(inventory):
    return _dump_task(
        "Configure Journeyman Inventory: {}".format(inventory.name),
        "journeyman.configuration.inventory",
        inventory_configuration_params(inventory),
    )


def repository_configuration_yaml(repository):
    params = {
        "name": repository.name,
        "description": repository.description or "",
        "repository_type": repository.repository_type,
        "url": repository.url or "",
        "directory_path": repository.directory_path or "",
        "default_branch": repository.default_branch or "main",
        "credential": "",
        "state": "present",
    }
    if repository.credential_id:
        from app.models import Credential
        credential = db.session.get(Credential, repository.credential_id)
        if credential is not None:
            params["credential"] = credential.name
    return _dump_task(
        "Configure Journeyman Repository: {}".format(repository.name),
        "journeyman.configuration.repository",
        params,
    )


def credential_configuration_yaml(credential):
    document = credential_configuration_document(credential)
    populated = document.pop("populated_secret_fields", [])
    document.pop("owner", None)
    document.pop("id", None)
    data = dict(document.get("credential_data") or {})
    for field in populated:
        data[field] = "{{ vault_journeyman_%s_%s }}" % (
            credential.name.lower().replace(" ", "_"), field.lower()
        )
    document["credential_data"] = data
    document["state"] = "present"
    return _dump_task(
        "Configure Journeyman Credential: {}".format(credential.name),
        "journeyman.configuration.credential",
        document,
    )


def schedule_configuration_yaml(schedule):
    return _configuration_yaml(
        "Schedule", "schedule", schedule.name, schedule_configuration_document(schedule)
    )


def reactor_configuration_yaml(reactor):
    return _configuration_yaml(
        "Reactor", "reactor", reactor.name, reactor_configuration_document(reactor)
    )


def signal_source_configuration_yaml(source):
    document = signal_source_configuration_document(source)
    configured = document.pop("hmac_secret_configured", False)
    if configured:
        document["hmac_secret"] = "{{ vault_journeyman_%s_hmac_secret }}" % source.name.lower().replace(" ", "_")
    return _configuration_yaml(
        "Signal Source", "signal_source", source.name, document
    )
