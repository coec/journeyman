"""Load Journeyman's YAML application configuration into process settings."""

import os
from pathlib import Path

import yaml


DEFAULT_CONFIGURATION_FILE = Path("/etc/journeyman/journeyman.yml")
SUPPORTED_CONFIGURATION_VERSION = 1

# YAML path -> legacy process setting. The YAML file is authoritative for all
# settings it defines. Environment variables remain an internal compatibility
# mechanism for existing Flask/application configuration code, but stale shell
# or legacy .env values must not override YAML.
_SETTING_MAP = {
    ("application", "config_class"): "JOURNEYMAN_CONFIG_CLASS",
    ("security", "secret_key"): "JOURNEYMAN_SECRET_KEY",
    ("security", "session_signing_key_file"): "JOURNEYMAN_SESSION_SIGNING_KEY_FILE",
    ("security", "session_signing_key_metadata_file"): "JOURNEYMAN_SESSION_SIGNING_KEY_METADATA_FILE",
    ("security", "credential_key_file"): "JOURNEYMAN_CREDENTIAL_KEY_FILE",
    ("security", "credential_keyring_dir"): "JOURNEYMAN_CREDENTIAL_KEYRING_DIR",
    ("security", "credential_active_key_file"): "JOURNEYMAN_CREDENTIAL_ACTIVE_KEY_FILE",
    ("authentication", "disabled"): "JOURNEYMAN_AUTHENTICATION_DISABLED",
    ("authentication", "fallback_admin_username"): "JOURNEYMAN_FALLBACK_ADMIN_USERNAME",
    ("authentication", "fallback_admin_password_hash_file"): "JOURNEYMAN_FALLBACK_ADMIN_PASSWORD_HASH_FILE",
    ("authentication", "fallback_admin_lifetime_minutes"): "JOURNEYMAN_FALLBACK_ADMIN_LIFETIME_MINUTES",
    ("authentication", "directory_admin_group_name"): "JOURNEYMAN_DIRECTORY_ADMIN_GROUP_NAME",
    ("authentication", "directory_user_group_name"): "JOURNEYMAN_DIRECTORY_USER_GROUP_NAME",
    ("authentication", "session_absolute_lifetime_seconds"): "JOURNEYMAN_AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS",
    ("authentication", "session_directory_revalidation_seconds"): "JOURNEYMAN_AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS",
    ("authentication", "login_rate_limit_attempts"): "JOURNEYMAN_LOGIN_RATE_LIMIT_ATTEMPTS",
    ("authentication", "login_rate_limit_window_seconds"): "JOURNEYMAN_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    ("database", "uri"): "JOURNEYMAN_DATABASE_URI",
    ("database", "pool_timeout_seconds"): "JOURNEYMAN_DATABASE_POOL_TIMEOUT_SECONDS",
    ("web", "public_fqdn"): "JOURNEYMAN_PUBLIC_FQDN",
    ("web", "tls_root"): "JOURNEYMAN_TLS_ROOT",
    ("web", "tls_certificate_path"): "JOURNEYMAN_TLS_CERTIFICATE_PATH",
    ("web", "tls_private_key_path"): "JOURNEYMAN_TLS_PRIVATE_KEY_PATH",
    ("web", "tls_chain_path"): "JOURNEYMAN_TLS_CHAIN_PATH",
    ("web", "https_port"): "JOURNEYMAN_HTTPS_PORT",
    ("paths", "repository_root"): "JOURNEYMAN_REPOSITORY_ROOT",
    ("paths", "runner_artifact_root"): "JOURNEYMAN_RUNNER_ARTIFACT_ROOT",
    ("paths", "log_root"): "JOURNEYMAN_LOG_ROOT",
    ("paths", "job_root"): "JOURNEYMAN_JOB_ROOT",
    ("paths", "inventory_snapshot_root"): "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
    ("paths", "signal_spool_root"): "JOURNEYMAN_SIGNAL_SPOOL_ROOT",
    ("environments", "application_path"): "JOURNEYMAN_APPLICATION_ENVIRONMENT_PATH",
    ("environments", "managed_root"): "JOURNEYMAN_MANAGED_ENVIRONMENT_ROOT",
    ("environments", "python_interpreters"): "JOURNEYMAN_ENVIRONMENT_PYTHONS",
    ("execution", "job_timeout_seconds"): "JOURNEYMAN_JOB_TIMEOUT_SECONDS",
    ("execution", "git_timeout_seconds"): "JOURNEYMAN_GIT_TIMEOUT_SECONDS",
    ("retention", "job_days"): "JOURNEYMAN_JOB_RETENTION_DAYS",
    ("retention", "reaction_days"): "JOURNEYMAN_REACTION_RETENTION_DAYS",
    ("retention", "inventory_cache_seconds"): "JOURNEYMAN_INVENTORY_CACHE_RETENTION_SECONDS",
    ("retention", "purge_interval_seconds"): "JOURNEYMAN_DATA_RETENTION_PURGE_INTERVAL_SECONDS",
    ("outbound", "allowed_hosts"): "JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
    ("rate_limits", "window_seconds"): "JOURNEYMAN_COSTLY_OPERATION_WINDOW_SECONDS",
    ("rate_limits", "preview_user_limit"): "JOURNEYMAN_COSTLY_PREVIEW_USER_LIMIT",
    ("rate_limits", "preview_global_limit"): "JOURNEYMAN_COSTLY_PREVIEW_GLOBAL_LIMIT",
    ("rate_limits", "dispatch_user_limit"): "JOURNEYMAN_COSTLY_LAUNCH_USER_LIMIT",
    ("rate_limits", "dispatch_global_limit"): "JOURNEYMAN_COSTLY_LAUNCH_GLOBAL_LIMIT",
    ("rate_limits", "inventory_user_limit"): "JOURNEYMAN_COSTLY_INVENTORY_USER_LIMIT",
    ("rate_limits", "inventory_global_limit"): "JOURNEYMAN_COSTLY_INVENTORY_GLOBAL_LIMIT",
    ("rate_limits", "repository_user_limit"): "JOURNEYMAN_COSTLY_REPOSITORY_USER_LIMIT",
    ("rate_limits", "repository_global_limit"): "JOURNEYMAN_COSTLY_REPOSITORY_GLOBAL_LIMIT",
    ("rate_limits", "environment_user_limit"): "JOURNEYMAN_COSTLY_ENVIRONMENT_USER_LIMIT",
    ("rate_limits", "environment_global_limit"): "JOURNEYMAN_COSTLY_ENVIRONMENT_GLOBAL_LIMIT",
}

_ALLOWED_TOP_LEVEL = {path[0] for path in _SETTING_MAP} | {"version"}


def configuration_path(path=None):
    if path is not None:
        return Path(path)

    configured = str(os.environ.get("JOURNEYMAN_CONFIG", "") or "").strip()

    # Before YAML configuration was introduced, JOURNEYMAN_CONFIG selected the
    # Flask configuration class (normally app.config.ProductionConfig). Older
    # installed systemd units may therefore still export that value. Translate
    # it in-process so an upgrade does not mistake the Python class name for a
    # filesystem path. New deployments always use JOURNEYMAN_CONFIG as the YAML
    # filename and JOURNEYMAN_CONFIG_CLASS for the Flask configuration class.
    if configured.startswith("app.config.") and "/" not in configured and "\\" not in configured:
        os.environ.setdefault("JOURNEYMAN_CONFIG_CLASS", configured)
        configured = str(DEFAULT_CONFIGURATION_FILE)
        os.environ["JOURNEYMAN_CONFIG"] = configured

    return Path(configured or DEFAULT_CONFIGURATION_FILE)


def _string_value(value, *, key):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    if value is None:
        return ""
    if isinstance(value, list):
        if not all(isinstance(item, (str, int, float)) for item in value):
            raise ValueError(f"{key} must contain only scalar values")
        return ",".join(str(item) for item in value)
    raise ValueError(f"{key} must be a scalar value or list of scalar values")


def load_journeyman_configuration(path=None, *, required=False):
    """Load YAML configuration and make defined YAML values authoritative."""

    explicit_path = path is not None
    config_path = configuration_path(path)
    if not config_path.is_file():
        if required or (not explicit_path and os.environ.get("JOURNEYMAN_CONFIG")):
            raise ValueError(f"Journeyman configuration file does not exist: {config_path}")
        return False

    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read Journeyman configuration {config_path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ValueError(f"{config_path}: YAML document root must be a mapping")

    version = document.get("version")
    if version != SUPPORTED_CONFIGURATION_VERSION:
        raise ValueError(
            f"{config_path}: configuration version must be {SUPPORTED_CONFIGURATION_VERSION}"
        )

    unknown_top = sorted(set(document) - _ALLOWED_TOP_LEVEL)
    if unknown_top:
        raise ValueError(f"{config_path}: unknown top-level key(s): {', '.join(unknown_top)}")

    known_children = {}
    for section, name in _SETTING_MAP:
        known_children.setdefault(section, set()).add(name)

    for section, allowed_names in known_children.items():
        section_value = document.get(section, {})
        if section_value is None:
            continue
        if not isinstance(section_value, dict):
            raise ValueError(f"{config_path}: {section} must be a mapping")
        unknown = sorted(set(section_value) - allowed_names)
        if unknown:
            raise ValueError(
                f"{config_path}: unknown {section} key(s): {', '.join(unknown)}"
            )

    for yaml_path, environment_name in _SETTING_MAP.items():
        section, name = yaml_path
        section_value = document.get(section) or {}
        if name not in section_value:
            continue
        os.environ[environment_name] = _string_value(
            section_value[name],
            key="{}.{}".format(section, name),
        )

    return True
