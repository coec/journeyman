"""
Resolve stored static Ansible inventories.
"""

import json
import os
import subprocess
import tempfile


ANSIBLE_INVENTORY_COMMAND = "/usr/bin/ansible-inventory"


class StaticInventoryError(Exception):
    """
    Raised when a static inventory cannot be resolved.
    """


def resolve_static_inventory(
    *,
    content,
    timeout=60,
):
    """
    Convert stored Ansible YAML inventory content into canonical
    ``ansible-inventory --list`` JSON.
    """

    content = str(content or "").strip()

    if not content:
        raise StaticInventoryError(
            "Static inventory content is empty."
        )

    inventory_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="journeyman-static-",
            suffix=".yml",
            delete=False,
        ) as inventory_file:
            inventory_path = inventory_file.name

            os.chmod(
                inventory_path,
                0o600,
            )

            inventory_file.write(content)
            inventory_file.write("\n")

        result = subprocess.run(
            [
                ANSIBLE_INVENTORY_COMMAND,
                "--inventory",
                inventory_path,
                "--list",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

        if result.returncode != 0:
            message = (
                result.stderr.strip()
                or "ansible-inventory failed."
            )

            message = message.replace(
                inventory_path,
                "<temporary static inventory>",
            )

            raise StaticInventoryError(
                "Unable to resolve static inventory: "
                + message
            )

        try:
            inventory_data = json.loads(
                result.stdout
            )

        except json.JSONDecodeError as exc:
            raise StaticInventoryError(
                "ansible-inventory returned invalid JSON."
            ) from exc

        if not isinstance(inventory_data, dict):
            raise StaticInventoryError(
                "Static inventory produced an invalid result."
            )

        hostvars = (
            inventory_data
            .get("_meta", {})
            .get("hostvars", {})
        )

        if not isinstance(hostvars, dict):
            raise StaticInventoryError(
                "Static inventory produced no hostvars mapping."
            )

        # Journeyman also accepts an explicit ``vars`` mapping beneath a
        # static-inventory host.  Native Ansible YAML inventory normally puts
        # host variables directly beneath the hostname, but the nested form is
        # convenient in Journeyman's editor and makes the intent unambiguous.
        # ansible-inventory returns that mapping as a host variable literally
        # named ``vars``; flatten it here so routing metadata such as
        # ``journeyman_runner`` and ordinary variables have normal host-var
        # semantics everywhere else in Journeyman.  Direct host variables win
        # if both forms specify the same key.
        for host, variables in hostvars.items():
            if not isinstance(variables, dict):
                continue

            nested_variables = variables.pop("vars", None)
            if not isinstance(nested_variables, dict):
                continue

            for key, value in nested_variables.items():
                variables.setdefault(key, value)

        return inventory_data

    except subprocess.TimeoutExpired as exc:
        raise StaticInventoryError(
            "Static inventory resolution timed out."
        ) from exc

    except OSError as exc:
        raise StaticInventoryError(
            "Unable to execute ansible-inventory: "
            + str(exc)
        ) from exc

    finally:
        if inventory_path:
            try:
                os.remove(inventory_path)
            except OSError:
                pass
