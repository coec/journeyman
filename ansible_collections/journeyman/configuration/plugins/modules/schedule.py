"""Manage Journeyman Project schedules declaratively."""

DOCUMENTATION = r'''
module: schedule
short_description: Configure a Journeyman Project schedule
options:
  name:
    type: str
    required: true
    description:
    - Name of the Journeyman resource.
  project:
    type: str
    required: true
    description:
    - Name of the Journeyman Project.
  schedule_type:
    type: str
    choices:
    - once
    - daily
    - weekly
    - interval
    default: once
    description:
    - Schedule recurrence type.
  timezone:
    type: str
    default: UTC
    description:
    - IANA timezone name used to interpret schedule times.
  start_at:
    type: str
    description:
    - Local date and time at which the schedule becomes eligible to run.
  end_at:
    type: str
    default: ''
    description:
    - Optional local date and time after which the recurring schedule stops.
  interval_minutes:
    type: int
    description:
    - Interval in minutes for an interval schedule.
  weekdays:
    type: list
    elements: int
    default: []
    description:
    - Weekday numbers used by a weekly schedule.
  enabled:
    type: bool
    default: true
    description:
    - Whether the Journeyman resource is enabled.
  state:
    type: str
    choices:
    - present
    - absent
    default: present
    description:
    - Desired presence or absence of the Journeyman resource.
  journeyman_url:
    type: str
    description:
    - Base URL of the Journeyman server. Defaults to the C(JOURNEYMAN_URL) environment variable.
  validate_certs:
    type: bool
    default: true
    description:
    - Whether to validate the TLS certificate presented by the Journeyman server.
  timeout:
    type: int
    default: 30
    description:
    - HTTP request timeout in seconds.
  api_token:
    type: str
    no_log: true
    description:
    - Journeyman API bearer token. Defaults to the C(JOURNEYMAN_API_TOKEN) environment variable.
description:
- Declares the desired configuration of a Journeyman Project Schedule.
- Schedules can be created, updated idempotently, disabled, or removed through the Journeyman API.
version_added: 0.1.0
author:
- Journeyman contributors
'''


EXAMPLES = r'''
- name: Configure a weekly Project schedule
  journeyman.configuration.schedule:
    name: Weekday maintenance
    project: Maintenance
    schedule_type: weekly
    timezone: Australia/Perth
    start_at: "2026-09-07T02:00"
    end_at: "2026-12-31T23:59"
    weekdays:
      - 0
      - 2
      - 4
    enabled: true
    state: present
    journeyman_url: https://journeyman.example/
    api_token: "{{ vault_journeyman_api_token }}"
    validate_certs: true
    timeout: 60

- name: Configure an interval schedule
  journeyman.configuration.schedule:
    name: Every two hours
    project: Inventory refresh
    schedule_type: interval
    timezone: UTC
    start_at: "2026-09-07T00:00"
    interval_minutes: 120
    enabled: true
    state: present

- name: Configure a one-time schedule
  journeyman.configuration.schedule:
    name: Sunday change
    project: Planned change
    schedule_type: once
    timezone: Australia/Perth
    start_at: "2026-09-13T01:00"
    state: present

- name: Remove a schedule
  journeyman.configuration.schedule:
    name: Retired schedule
    project: Maintenance
    state: absent
'''

RETURN = r'''
changed: {description: Whether Journeyman changed the resource, returned: always, type: bool}
schedule: {description: Resulting Schedule configuration, returned: when available, type: dict}
message: {description: Configuration result message, returned: when available, type: str}
'''



def execute(params, client):
    name = params["name"]
    project = params["project"]
    if params.get("state", "present") == "absent":
        return client.request(
            "DELETE",
            "/api/v1/schedule-configurations/by-name",
            query={"project": project, "name": name},
        )

    payload = {
        "name": name,
        "project": project,
        "schedule_type": params.get("schedule_type", "once"),
        "timezone": params.get("timezone", "UTC"),
        "start_at": params.get("start_at") or "",
        "end_at": params.get("end_at") or "",
        "interval_minutes": params.get("interval_minutes"),
        "weekdays": params.get("weekdays") or [],
        "enabled": params.get("enabled", True),
    }
    return client.request(
        "PUT",
        "/api/v1/schedule-configurations/by-name",
        payload=payload,
    )


def main():
    from ansible.module_utils.basic import AnsibleModule
    from ansible_collections.journeyman.configuration.plugins.module_utils.journeyman_api import (
        JourneymanApiClient,
        JourneymanApiError,
    )

    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "project": {"type": "str", "required": True},
            "schedule_type": {"type": "str", "choices": ["once", "daily", "weekly", "interval"], "default": "once"},
            "timezone": {"type": "str", "default": "UTC"},
            "start_at": {"type": "str"},
            "end_at": {"type": "str", "default": ""},
            "interval_minutes": {"type": "int"},
            "weekdays": {"type": "list", "elements": "int", "default": []},
            "enabled": {"type": "bool", "default": True},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
            "journeyman_url": {"type": "str"},
            "api_token": {"type": "str", "no_log": True},
            "validate_certs": {"type": "bool", "default": True},
            "timeout": {"type": "int", "default": 30},
        },
        required_if=[["state", "present", ["start_at"]]],
        supports_check_mode=False,
    )
    try:
        client = JourneymanApiClient(
            url=module.params["journeyman_url"],
            token=module.params["api_token"],
            validate_certs=module.params["validate_certs"],
            timeout=module.params["timeout"],
        )
        module.exit_json(**execute(module.params, client))
    except JourneymanApiError as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
