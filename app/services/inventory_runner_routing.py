"""Resolve and validate Journeyman runner routing from inventory metadata."""

from app.models import Runner, RunnerCrew


class InventoryRunnerRoutingError(Exception):
    """Inventory routing metadata is missing, mixed, or invalid."""


LOCAL_HOST_NAMES = {"localhost", "127.0.0.1", "::1"}


def _clean(value):
    return str(value or "").strip()


def _hostvars(inventory_data):
    if not isinstance(inventory_data, dict):
        raise InventoryRunnerRoutingError("Resolved inventory is invalid.")

    value = inventory_data.get("_meta", {}).get("hostvars")
    if not isinstance(value, dict):
        raise InventoryRunnerRoutingError(
            "Resolved inventory has no hostvars mapping."
        )

    return value


def _is_local_target(host, variables):
    host_name = _clean(host).lower()
    connection = _clean((variables or {}).get("ansible_connection")).lower()
    return host_name in LOCAL_HOST_NAMES or connection == "local"


def _runner_reference(variables):
    """
    Return the host's explicit runner reference, if any.

    Static inventories may expose ``journeyman_runner`` directly as a host
    variable. Foreman/Satellite inventory exposes host parameters beneath the
    normal ``foreman_params`` mapping. Zabbix keeps tags under
    ``zabbix.tags_by_name``; the inventory resolver also promotes the normal
    single-value case to a top-level host variable.

    If both a direct host variable and a Foreman parameter are present, they
    must agree. Silently choosing one would make runner routing ambiguous.
    """

    variables = variables or {}
    direct = _clean(variables.get("journeyman_runner"))

    foreman = variables.get("foreman_params")
    if not isinstance(foreman, dict):
        foreman = {}
    foreman_value = _clean(foreman.get("journeyman_runner"))

    explicit_values = []
    for value in (direct, foreman_value):
        if (
            value
            and value.lower()
            not in {item.lower() for item in explicit_values}
        ):
            explicit_values.append(value)

    if len(explicit_values) > 1:
        raise InventoryRunnerRoutingError(
            "Host has conflicting journeyman_runner values from inventory "
            "host variables and Foreman/Satellite parameters: {}.".format(
                ", ".join(sorted(explicit_values))
            )
        )

    if explicit_values:
        return explicit_values[0]

    zabbix = variables.get("zabbix")
    if not isinstance(zabbix, dict):
        return ""

    tags_by_name = zabbix.get("tags_by_name")
    if not isinstance(tags_by_name, dict):
        return ""

    raw_values = tags_by_name.get("journeyman_runner", [])
    if not isinstance(raw_values, list):
        raw_values = [raw_values]

    values = []
    for raw_value in raw_values:
        value = _clean(raw_value)
        if value and value.lower() not in {item.lower() for item in values}:
            values.append(value)

    if len(values) > 1:
        raise InventoryRunnerRoutingError(
            "Zabbix host has multiple different journeyman_runner tag values: "
            "{}.".format(", ".join(sorted(values)))
        )

    return values[0] if values else ""


def registered_remote_runner(reference):
    """Resolve a configured runner name, UUID or hostname to one valid runner."""

    requested = _clean(reference)
    if not requested:
        return None

    requested_lower = requested.lower()
    candidates = (
        Runner.query
        .filter(
            Runner.enabled.is_(True),
            Runner.is_local.is_(False),
        )
        .all()
    )

    for runner in candidates:
        identities = {
            _clean(runner.name).lower(),
            _clean(runner.runner_uuid).lower(),
            _clean(runner.hostname).lower(),
        }
        if requested_lower in identities and runner.is_registered:
            return runner

    return None


def validate_default_runner(project):
    """Validate a Project's selected default runner at execution time."""

    if project.default_runner_id is None:
        return None

    runner = Runner.query.get(project.default_runner_id)
    if (
        runner is None
        or runner.is_local
        or not runner.enabled
        or not runner.is_registered
    ):
        raise InventoryRunnerRoutingError(
            "The Project default runner is no longer an enabled registered "
            "remote runner. Edit the Project and select a valid default runner."
        )

    return runner


def validate_default_runner_crew(project):
    """Validate a Project's selected default Runner Crew configuration."""

    if getattr(project, "default_runner_crew_id", None) is None:
        return None

    crew = RunnerCrew.query.get(project.default_runner_crew_id)
    if crew is None or not crew.enabled:
        raise InventoryRunnerRoutingError(
            "The Project default Runner Crew no longer exists or is disabled. "
            "Edit the Project and select a valid execution destination."
        )
    if not crew.runners:
        raise InventoryRunnerRoutingError(
            'The Project default Runner Crew "{}" has no members.'.format(crew.name)
        )
    return crew


def validate_inventory_runner_overrides(resolved_inventory_data):
    """
    Validate every explicit journeyman_runner reference currently resolvable.

    Returns a mapping of host name to Runner for hosts with an explicit
    override. Hosts without an override intentionally do not appear; they use
    the Project default runner when execution slices are built.
    """

    assignments = {}

    for inventory_data in (resolved_inventory_data or {}).values():
        for host, variables in _hostvars(inventory_data).items():
            reference = _runner_reference(variables)
            if not reference:
                continue

            runner = registered_remote_runner(reference)
            if runner is None:
                raise InventoryRunnerRoutingError(
                    'Host "{}" requests runner "{}", but no enabled registered '
                    "remote runner with that name, hostname or UUID exists."
                    .format(host, reference)
                )

            assignments[str(host)] = runner

    return assignments


def derive_inventory_runner_routing(resolved_inventory_data):
    """
    Derive legacy single-runner Job routing from inventory metadata.

    This remains for compatibility while execution fan-out is moved from the
    Job level to per-step execution slices. Explicit runner references are
    validated using the same rules as the new preflight path.
    """

    hosts = []

    for inventory_data in (resolved_inventory_data or {}).values():
        for host, variables in _hostvars(inventory_data).items():
            hosts.append((str(host), variables or {}))

    if not hosts:
        raise InventoryRunnerRoutingError(
            "Inventory routing requires at least one resolved host."
        )

    local_hosts = [
        host
        for host, variables in hosts
        if _is_local_target(host, variables)
    ]

    if len(local_hosts) == len(hosts):
        return {
            "dispatch_target": "local",
            "required_runner_site": "",
            "required_runner_id": None,
        }

    if local_hosts:
        raise InventoryRunnerRoutingError(
            "Inventory routing cannot mix localhost targets with remote "
            "targets in one Job."
        )

    runner_values = []
    site_values = []

    for host, variables in hosts:
        runner_name = _runner_reference(variables)
        site_name = _clean(variables.get("journeyman_site"))

        if runner_name:
            runner_values.append((host, runner_name))

        if site_name:
            site_values.append((host, site_name))

    if runner_values:
        missing = [
            host
            for host, variables in hosts
            if not _runner_reference(variables)
        ]

        if missing:
            raise InventoryRunnerRoutingError(
                "Inventory routing uses journeyman_runner but it is missing "
                "from host(s): {}.".format(", ".join(sorted(missing)))
            )

        runners = {}
        for host, reference in runner_values:
            runner = registered_remote_runner(reference)
            if runner is None:
                raise InventoryRunnerRoutingError(
                    'Host "{}" requests runner "{}", but no enabled registered '
                    "remote runner with that name, hostname or UUID exists."
                    .format(host, reference)
                )
            runners[runner.id] = runner

        if len(runners) != 1:
            raise InventoryRunnerRoutingError(
                "A single Job cannot currently span multiple Journeyman "
                "runners: {}.".format(
                    ", ".join(sorted(runner.name for runner in runners.values()))
                )
            )

        runner = next(iter(runners.values()))
        return {
            "dispatch_target": "remote",
            "required_runner_site": "",
            "required_runner_id": runner.id,
        }

    missing = [
        host
        for host, variables in hosts
        if not _clean(variables.get("journeyman_site"))
    ]

    if missing:
        raise InventoryRunnerRoutingError(
            "Inventory routing requires journeyman_site or "
            "journeyman_runner for every remote host. Missing host(s): {}."
            .format(", ".join(sorted(missing)))
        )

    distinct_sites = {
        value.lower(): value
        for _host, value in site_values
    }

    if len(distinct_sites) != 1:
        raise InventoryRunnerRoutingError(
            "A single Job cannot currently span multiple Journeyman sites: "
            "{}.".format(
                ", ".join(sorted(set(value for _host, value in site_values)))
            )
        )

    return {
        "dispatch_target": "remote",
        "required_runner_site": next(iter(distinct_sites.values())),
        "required_runner_id": None,
    }
