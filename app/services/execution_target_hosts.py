"""Resolve the effective host set for one execution step using Ansible itself."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from flask import current_app

from .job_inventory_snapshot import (
    JobInventorySnapshotError,
    build_inventory_script_bytes,
)


class ExecutionTargetResolutionError(Exception):
    """Ansible could not safely resolve a step's effective target hosts."""


def _hosts_from_inventory_output(inventory_output):
    hosts = set()

    meta = inventory_output.get("_meta", {})
    hostvars = meta.get("hostvars", {})
    if isinstance(hostvars, dict):
        hosts.update(str(host) for host in hostvars)

    for group_name, group_data in inventory_output.items():
        if group_name == "_meta" or not isinstance(group_data, dict):
            continue
        group_hosts = group_data.get("hosts", [])
        if isinstance(group_hosts, list):
            hosts.update(str(host) for host in group_hosts)

    return tuple(sorted(hosts))


def _hosts_from_playbook_list_output(output):
    hosts = []
    collecting = False

    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()

        if line.startswith("hosts (") and line.endswith("):"):
            collecting = True
            continue

        if not collecting:
            continue

        if not raw_line.startswith("      "):
            break

        if line:
            hosts.append(line)

    return tuple(sorted(set(hosts)))


def target_hosts_for_inventory(inventory_data, limit=""):
    """Resolve the effective hosts for an inventory and optional Ansible limit.

    ``ansible-inventory`` does not implement ``--limit``.  For an unlimited
    inventory, use its JSON output directly.  When a limit is present, ask
    ``ansible-playbook --list-hosts`` to evaluate the host pattern using
    Ansible's own pattern engine.

    Temporary inventory/playbook files may describe sensitive execution
    targets, so they are created under a mode-0700 directory and removed
    before this function returns.
    """

    try:
        script_bytes = build_inventory_script_bytes(inventory_data)
    except JobInventorySnapshotError as exc:
        raise ExecutionTargetResolutionError(
            "Unable to prepare inventory data for target resolution."
        ) from exc

    executable = current_app.config.get(
        "ANSIBLE_INVENTORY_EXECUTABLE",
        "/usr/bin/ansible-inventory",
    )
    timeout_seconds = int(
        current_app.config.get("PROJECT_RUN_PREVIEW_TIMEOUT_SECONDS", 30)
    )

    with tempfile.TemporaryDirectory(prefix="journeyman-target-resolution-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        os.chmod(temporary_path, 0o700)

        inventory_script = temporary_path / "inventory.py"
        inventory_script.write_bytes(script_bytes)
        os.chmod(inventory_script, 0o700)

        effective_limit = str(limit or "").strip()

        if effective_limit:
            playbook = temporary_path / "list-hosts.yml"
            playbook.write_text(
                "---\n"
                "- name: Journeyman target resolution\n"
                "  hosts: all\n"
                "  gather_facts: false\n"
                "  tasks: []\n",
                encoding="utf-8",
            )
            os.chmod(playbook, 0o600)

            playbook_executable = str(
                Path(executable).with_name("ansible-playbook")
            )
            command = [
                playbook_executable,
                "--inventory",
                str(inventory_script),
                "--list-hosts",
                "--limit",
                effective_limit,
                str(playbook),
            ]
        else:
            command = [
                executable,
                "--inventory",
                str(inventory_script),
                "--list",
            ]

        environment = dict(os.environ)
        environment["ANSIBLE_NOCOLOR"] = "1"

        try:
            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise ExecutionTargetResolutionError(
                "The ansible-inventory executable was not found."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutionTargetResolutionError(
                "Target resolution exceeded its timeout of {} seconds.".format(
                    timeout_seconds
                )
            ) from exc

        if result.returncode != 0:
            detail = (
                result.stderr
                or result.stdout
                or "Unknown ansible-inventory error."
            ).strip()[-600:]
            raise ExecutionTargetResolutionError(
                "Ansible could not calculate the target hosts: {}".format(detail)
            )

        if effective_limit:
            return _hosts_from_playbook_list_output(result.stdout)

        try:
            inventory_output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ExecutionTargetResolutionError(
                "ansible-inventory returned invalid JSON."
            ) from exc

    return _hosts_from_inventory_output(inventory_output)
