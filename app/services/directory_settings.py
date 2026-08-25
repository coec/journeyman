import ipaddress
import re
from pathlib import Path

from flask import current_app

from app import db
from app.models import DirectoryServer, DirectorySetting
from app.models.directory import (
    DEFAULT_ADMIN_GROUP_NAME,
    DEFAULT_USER_GROUP_NAME,
    DIRECTORY_SETTING_ID,
)


HOST_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class DirectorySettingsValidationError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__(" ".join(self.errors))


def _clean(value):
    return str(value or "").strip()


def normalize_server_host(value):
    value = _clean(value).lower().rstrip(".")

    if not value:
        raise ValueError(
            "Directory server hostname is required."
        )

    if len(value) > 253:
        raise ValueError(
            "Directory server hostname cannot exceed 253 characters."
        )

    try:
        ipaddress.ip_address(value)
    except ValueError:
        labels = value.split(".")

        for label in labels:
            if not HOST_LABEL_PATTERN.fullmatch(label):
                raise ValueError(
                    "Directory server hostname is invalid."
                )

    return value


def default_directory_settings_values():
    return {
        "enabled": False,
        "base_dn": "",
        "user_search_base": "",
        "group_search_base": "",
        "bind_username": "",
        "bind_password": "",
        "ca_certificate_path": "",
        "connect_timeout_seconds": "3",
        "operation_timeout_seconds": "10",
        "administrator_group_name": current_app.config.get(
            "DIRECTORY_ADMIN_GROUP_NAME",
            DEFAULT_ADMIN_GROUP_NAME,
        ),
        "user_group_name": current_app.config.get(
            "DIRECTORY_USER_GROUP_NAME",
            DEFAULT_USER_GROUP_NAME,
        ),
        "include_nested_groups": True,
        "servers": [
            {
                "host": "",
                "port": "636",
                "use_ssl": True,
                "enabled": True,
            },
            {
                "host": "",
                "port": "636",
                "use_ssl": True,
                "enabled": True,
            },
        ],
        "has_bind_password": False,
    }


def directory_settings_form_data(form):
    hosts = form.getlist("server_host")
    ports = form.getlist("server_port")
    ssl_positions = {
        _clean(value)
        for value in form.getlist("server_use_ssl")
    }
    enabled_positions = {
        _clean(value)
        for value in form.getlist("server_enabled")
    }

    count = max(
        len(hosts),
        len(ports),
        2,
    )

    servers = []

    for index in range(count):
        position = str(index + 1)
        servers.append(
            {
                "host": (
                    _clean(hosts[index])
                    if index < len(hosts)
                    else ""
                ),
                "port": (
                    _clean(ports[index])
                    if index < len(ports)
                    else "636"
                ),
                "use_ssl": position in ssl_positions,
                "enabled": position in enabled_positions,
            }
        )

    return {
        "enabled": form.get("enabled") == "on",
        "base_dn": _clean(form.get("base_dn")),
        "user_search_base": _clean(
            form.get("user_search_base")
        ),
        "group_search_base": _clean(
            form.get("group_search_base")
        ),
        "bind_username": _clean(
            form.get("bind_username")
        ),
        "bind_password": str(
            form.get("bind_password") or ""
        ),
        "ca_certificate_path": _clean(
            form.get("ca_certificate_path")
        ),
        "connect_timeout_seconds": _clean(
            form.get("connect_timeout_seconds")
        ),
        "operation_timeout_seconds": _clean(
            form.get("operation_timeout_seconds")
        ),
        "administrator_group_name": _clean(
            form.get("administrator_group_name")
        ),
        "user_group_name": _clean(
            form.get("user_group_name")
        ),
        "include_nested_groups": (
            form.get("include_nested_groups") == "on"
        ),
        "servers": servers,
        "has_bind_password": (
            form.get("has_bind_password") == "1"
        ),
    }


def settings_to_form_data(settings):
    server_rows = [
        {
            "host": server.host,
            "port": str(server.port),
            "use_ssl": server.use_ssl,
            "enabled": server.enabled,
        }
        for server in settings.servers
    ]

    while len(server_rows) < 2:
        server_rows.append(
            {
                "host": "",
                "port": "636",
                "use_ssl": True,
                "enabled": True,
            }
        )

    return {
        "enabled": settings.enabled,
        "base_dn": settings.base_dn,
        "user_search_base": settings.user_search_base,
        "group_search_base": settings.group_search_base,
        "bind_username": settings.bind_username,
        "bind_password": "",
        "ca_certificate_path": (
            settings.ca_certificate_path
        ),
        "connect_timeout_seconds": str(
            settings.connect_timeout_seconds
        ),
        "operation_timeout_seconds": str(
            settings.operation_timeout_seconds
        ),
        "administrator_group_name": (
            settings.administrator_group_name
        ),
        "user_group_name": settings.user_group_name,
        "include_nested_groups": (
            settings.include_nested_groups
        ),
        "servers": server_rows,
        "has_bind_password": settings.has_bind_password(),
    }


def _normalize_dn(value, label, required):
    value = _clean(value)

    if not value:
        if required:
            raise ValueError(
                "{} is required.".format(label)
            )
        return ""

    if len(value) > 500:
        raise ValueError(
            "{} cannot exceed 500 characters.".format(label)
        )

    if "=" not in value:
        raise ValueError(
            "{} does not look like an LDAP distinguished name."
            .format(label)
        )

    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(
            "{} contains invalid characters.".format(label)
        )

    return value


def _normalize_timeout(value, label, minimum, maximum):
    try:
        result = int(_clean(value))
    except (TypeError, ValueError):
        raise ValueError(
            "{} must be a number.".format(label)
        )

    if not minimum <= result <= maximum:
        raise ValueError(
            "{} must be between {} and {} seconds."
            .format(label, minimum, maximum)
        )

    return result


def validate_directory_settings(values, *, existing_settings=None):
    errors = []
    normalized = {}

    enabled = bool(values.get("enabled"))
    normalized["enabled"] = enabled

    required = enabled

    dn_fields = (
        ("base_dn", "Base DN"),
        ("user_search_base", "User search base"),
        ("group_search_base", "Group search base"),
    )

    for field_name, label in dn_fields:
        try:
            normalized[field_name] = _normalize_dn(
                values.get(field_name),
                label,
                required,
            )
        except ValueError as exc:
            errors.append(str(exc))

    bind_username = _clean(
        values.get("bind_username")
    )

    if required and not bind_username:
        errors.append(
            "LDAP bind username is required."
        )
    elif len(bind_username) > 500:
        errors.append(
            "LDAP bind username cannot exceed 500 characters."
        )
    elif any(
        character in bind_username
        for character in ("\x00", "\r", "\n")
    ):
        errors.append(
            "LDAP bind username contains invalid characters."
        )

    normalized["bind_username"] = bind_username

    bind_password = str(
        values.get("bind_password") or ""
    )

    has_existing_password = bool(
        existing_settings is not None
        and existing_settings.has_bind_password()
    )

    if required and not bind_password and not has_existing_password:
        errors.append(
            "LDAP bind password is required."
        )
    elif len(bind_password) > 4096:
        errors.append(
            "LDAP bind password cannot exceed 4096 characters."
        )

    normalized["bind_password"] = bind_password

    ca_path = _clean(
        values.get("ca_certificate_path")
    )

    if required and not ca_path:
        errors.append(
            "LDAP CA certificate path is required."
        )
    elif any(
        character in ca_path
        for character in ("\x00", "\r", "\n")
    ):
        errors.append(
            "LDAP CA certificate path contains invalid characters."
        )
    elif len(ca_path) > 500:
        errors.append(
            "LDAP CA certificate path cannot exceed 500 characters."
        )
    elif ca_path:
        path = Path(ca_path)

        if not path.is_absolute():
            errors.append(
                "LDAP CA certificate path must be absolute."
            )
        else:
            normalized["ca_certificate_path"] = str(path)
    else:
        normalized["ca_certificate_path"] = ""

    timeout_fields = (
        (
            "connect_timeout_seconds",
            "LDAP connection timeout",
            1,
            30,
        ),
        (
            "operation_timeout_seconds",
            "LDAP operation timeout",
            1,
            120,
        ),
    )

    for field_name, label, minimum, maximum in timeout_fields:
        try:
            normalized[field_name] = _normalize_timeout(
                values.get(field_name),
                label,
                minimum,
                maximum,
            )
        except ValueError as exc:
            errors.append(str(exc))

    for field_name, label in (
        (
            "administrator_group_name",
            "Administrator AD group",
        ),
        (
            "user_group_name",
            "User AD group",
        ),
    ):
        value = _clean(values.get(field_name))

        if required and not value:
            errors.append(
                "{} is required.".format(label)
            )

        if len(value) > 255:
            errors.append(
                "{} cannot exceed 255 characters."
                .format(label)
            )
        elif any(
            character in value
            for character in ("\x00", "\r", "\n")
        ):
            errors.append(
                "{} contains invalid characters.".format(label)
            )

        normalized[field_name] = value

    if (
        normalized.get("administrator_group_name")
        and normalized.get("user_group_name")
        and normalized["administrator_group_name"].casefold()
        == normalized["user_group_name"].casefold()
    ):
        errors.append(
            "Administrator and User AD groups must be different."
        )

    normalized["include_nested_groups"] = bool(
        values.get("include_nested_groups")
    )

    normalized_servers = []
    seen_servers = set()

    for index, row in enumerate(
        values.get("servers") or (),
        start=1,
    ):
        raw_host = _clean(row.get("host"))
        row_enabled = bool(row.get("enabled"))

        if not raw_host:
            continue

        try:
            host = normalize_server_host(raw_host)
        except ValueError as exc:
            errors.append(
                "Server {}: {}".format(index, exc)
            )
            continue

        try:
            port = int(_clean(row.get("port")))
        except (TypeError, ValueError):
            errors.append(
                "Server {}: port must be a number."
                .format(index)
            )
            continue

        if not 1 <= port <= 65535:
            errors.append(
                "Server {}: port must be between 1 and 65535."
                .format(index)
            )
            continue

        key = (host.casefold(), port)

        if key in seen_servers:
            errors.append(
                "Directory server {}:{} is listed more than once."
                .format(host, port)
            )
            continue

        seen_servers.add(key)

        normalized_servers.append(
            {
                "position": len(normalized_servers) + 1,
                "host": host,
                "port": port,
                "use_ssl": bool(row.get("use_ssl")),
                "enabled": row_enabled,
            }
        )

    enabled_servers = [
        server
        for server in normalized_servers
        if server["enabled"]
    ]

    if enabled and len(enabled_servers) < 2:
        errors.append(
            "At least two enabled directory servers are required."
        )

    if enabled:
        for server in enabled_servers:
            if not server["use_ssl"]:
                errors.append(
                    "Enabled directory servers must use LDAPS."
                )
                break

    normalized["servers"] = normalized_servers

    if errors:
        raise DirectorySettingsValidationError(errors)

    return normalized


def get_or_create_directory_settings():
    settings = db.session.get(
        DirectorySetting,
        DIRECTORY_SETTING_ID,
    )

    if settings is not None:
        return settings

    defaults = default_directory_settings_values()

    settings = DirectorySetting(
        id=DIRECTORY_SETTING_ID,
        enabled=False,
        base_dn="",
        user_search_base="",
        group_search_base="",
        bind_username="",
        ca_certificate_path="",
        connect_timeout_seconds=3,
        operation_timeout_seconds=10,
        administrator_group_name=defaults[
            "administrator_group_name"
        ],
        user_group_name=defaults[
            "user_group_name"
        ],
        include_nested_groups=True,
        updated_by="system",
    )

    for index, row in enumerate(
        defaults["servers"],
        start=1,
    ):
        if not row["host"]:
            continue

        settings.servers.append(
            DirectoryServer(
                position=index,
                host=row["host"],
                port=int(row["port"]),
                use_ssl=row["use_ssl"],
                enabled=row["enabled"],
            )
        )

    db.session.add(settings)
    db.session.commit()

    return settings


def update_directory_settings(settings, values, *, updated_by):
    settings.enabled = values["enabled"]
    settings.base_dn = values["base_dn"]
    settings.user_search_base = values["user_search_base"]
    settings.group_search_base = values["group_search_base"]
    settings.bind_username = values["bind_username"]
    settings.ca_certificate_path = values[
        "ca_certificate_path"
    ]
    settings.connect_timeout_seconds = values[
        "connect_timeout_seconds"
    ]
    settings.operation_timeout_seconds = values[
        "operation_timeout_seconds"
    ]
    settings.administrator_group_name = values[
        "administrator_group_name"
    ]
    settings.user_group_name = values[
        "user_group_name"
    ]
    settings.include_nested_groups = values[
        "include_nested_groups"
    ]
    settings.updated_by = updated_by

    if values.get("bind_password"):
        settings.set_bind_password(
            values["bind_password"]
        )

    previous_test_results = {
        (server.host.casefold(), server.port): {
            "last_test_ok": server.last_test_ok,
            "last_test_message": server.last_test_message,
            "last_test_at": server.last_test_at,
        }
        for server in settings.servers
    }

    settings.servers[:] = []
    db.session.flush()

    for row in values["servers"]:
        previous = previous_test_results.get(
            (row["host"].casefold(), row["port"]),
            {},
        )

        settings.servers.append(
            DirectoryServer(
                position=row["position"],
                host=row["host"],
                port=row["port"],
                use_ssl=row["use_ssl"],
                enabled=row["enabled"],
                last_test_ok=previous.get(
                    "last_test_ok"
                ),
                last_test_message=previous.get(
                    "last_test_message",
                    "",
                ),
                last_test_at=previous.get(
                    "last_test_at"
                ),
            )
        )

    db.session.commit()
