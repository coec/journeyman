import os
import socket
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SESSION_SIGNING_KEY_FILE = Path("/etc/journeyman/session-signing.key")


def _load_session_signing_key():
    path = Path(os.environ.get("JOURNEYMAN_SESSION_SIGNING_KEY_FILE", str(DEFAULT_SESSION_SIGNING_KEY_FILE)))
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("Journeyman session-signing key file is empty: {}".format(path))
        mode = path.stat().st_mode
        if mode & 0o007:
            raise RuntimeError("Journeyman session-signing key must not be accessible by other users: {}".format(path))
        return value
    return os.environ.get("JOURNEYMAN_SECRET_KEY", "development-only-change-me")


class Config:
    """
    Base Journeyman configuration.

    YAML configuration is loaded from /etc/journeyman/journeyman.yml.
    Explicit JOURNEYMAN_* environment variables remain supported as
    per-process overrides.
    """

    SECRET_KEY = _load_session_signing_key()
    SESSION_SIGNING_KEY_FILE = os.environ.get(
        "JOURNEYMAN_SESSION_SIGNING_KEY_FILE",
        str(DEFAULT_SESSION_SIGNING_KEY_FILE),
    )
    SESSION_SIGNING_KEY_METADATA_FILE = os.environ.get(
        "JOURNEYMAN_SESSION_SIGNING_KEY_METADATA_FILE",
        "/etc/journeyman/session-signing-key.json",
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "JOURNEYMAN_DATABASE_URI",
        "sqlite:///journeyman.db",
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Database checkout/connection attempts must not block a web worker
    # indefinitely. PostgreSQL driver connection timeout is also documented
    # in INSTALL.PostgreSQL.md for deployments using a remote database.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_timeout": int(
            os.environ.get(
                "JOURNEYMAN_DATABASE_POOL_TIMEOUT_SECONDS",
                "10",
            )
        ),
    }

    AUTHENTICATION_DISABLED = os.environ.get(
        "JOURNEYMAN_AUTHENTICATION_DISABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    FALLBACK_ADMIN_USERNAME = os.environ.get(
        "JOURNEYMAN_FALLBACK_ADMIN_USERNAME",
        "admin",
    ).strip() or "admin"

    FALLBACK_ADMIN_PASSWORD_HASH_FILE = os.environ.get(
        "JOURNEYMAN_FALLBACK_ADMIN_PASSWORD_HASH_FILE",
        "/etc/journeyman/fallback-admin-password.hash",
    )

    FALLBACK_ADMIN_LIFETIME_MINUTES = int(
        os.environ.get("JOURNEYMAN_FALLBACK_ADMIN_LIFETIME_MINUTES", "60")
    )

    DIRECTORY_ADMIN_GROUP_NAME = (
        os.environ.get(
            "JOURNEYMAN_DIRECTORY_ADMIN_GROUP_NAME",
            "Journeyman Admins",
        ).strip()
        or "Journeyman Admins"
    )

    DIRECTORY_USER_GROUP_NAME = (
        os.environ.get(
            "JOURNEYMAN_DIRECTORY_USER_GROUP_NAME",
            "Journeyman Users",
        ).strip()
        or "Journeyman Users"
    )

    # Browser cookies must be able to outlive the longest selectable per-user
    # idle timeout. The server-side auth-session registry enforces each user's
    # actual inactivity limit.
    AUTH_SESSION_DEFAULT_IDLE_TIMEOUT_MINUTES = int(
        os.environ.get("JOURNEYMAN_AUTH_SESSION_DEFAULT_IDLE_TIMEOUT_MINUTES", "480")
    )
    AUTH_SESSION_MAX_IDLE_TIMEOUT_MINUTES = int(
        os.environ.get("JOURNEYMAN_AUTH_SESSION_MAX_IDLE_TIMEOUT_MINUTES", "10080")
    )
    PERMANENT_SESSION_LIFETIME = AUTH_SESSION_MAX_IDLE_TIMEOUT_MINUTES * 60

    # Explicit bound for the serialized signed browser session. Oversized
    # sessions are cleared rather than emitted and risking proxy/browser truncation.
    MAX_SESSION_COOKIE_BYTES = int(
        os.environ.get("JOURNEYMAN_MAX_SESSION_COOKIE_BYTES", "3072")
    )

    # Per-user inactivity is enforced by the server-side session registry.
    # An independent absolute maximum lifetime is retained as a safety bound.
    AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS = int(
        os.environ.get(
            "JOURNEYMAN_AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS",
            "2592000",
        )
    )

    AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS = int(
        os.environ.get(
            "JOURNEYMAN_AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS",
            "60",
        )
    )

    LOGIN_RATE_LIMIT_ATTEMPTS = int(
        os.environ.get("JOURNEYMAN_LOGIN_RATE_LIMIT_ATTEMPTS", "10")
    )
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(
        os.environ.get("JOURNEYMAN_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")
    )

    # Shared DB-backed limits for operations that can consume significant
    # local CPU/process capacity or trigger expensive backend work.
    COSTLY_OPERATION_WINDOW_SECONDS = int(
        os.environ.get("JOURNEYMAN_COSTLY_OPERATION_WINDOW_SECONDS", "300")
    )
    COSTLY_PREVIEW_USER_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_PREVIEW_USER_LIMIT", "20")
    )
    COSTLY_PREVIEW_GLOBAL_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_PREVIEW_GLOBAL_LIMIT", "100")
    )
    COSTLY_LAUNCH_USER_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_LAUNCH_USER_LIMIT", "10")
    )
    COSTLY_LAUNCH_GLOBAL_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_LAUNCH_GLOBAL_LIMIT", "50")
    )
    COSTLY_INVENTORY_USER_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_INVENTORY_USER_LIMIT", "10")
    )
    COSTLY_INVENTORY_GLOBAL_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_INVENTORY_GLOBAL_LIMIT", "50")
    )
    COSTLY_REPOSITORY_USER_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_REPOSITORY_USER_LIMIT", "5")
    )
    COSTLY_REPOSITORY_GLOBAL_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_REPOSITORY_GLOBAL_LIMIT", "20")
    )
    COSTLY_ENVIRONMENT_USER_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_ENVIRONMENT_USER_LIMIT", "3")
    )
    COSTLY_ENVIRONMENT_GLOBAL_LIMIT = int(
        os.environ.get("JOURNEYMAN_COSTLY_ENVIRONMENT_GLOBAL_LIMIT", "10")
    )

    OUTBOUND_ALLOWED_HOSTS = tuple(
        value.strip().lower()
        for value in os.environ.get(
            "JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
            "",
        ).split(",")
        if value.strip()
    )

    # Development/test remain permissive. ProductionConfig enables both
    # controls so runtime outbound requests fail closed unless explicitly
    # allowed by the sysadmin-owned environment configuration.
    OUTBOUND_ALLOWLIST_ENFORCED = False
    OUTBOUND_SECURE_TRANSPORT_ENFORCED = False

    PUBLIC_FQDN = os.environ.get(
        "JOURNEYMAN_PUBLIC_FQDN",
        socket.getfqdn(),
    ).strip().lower()

    TLS_ROOT = Path(
        os.environ.get(
            "JOURNEYMAN_TLS_ROOT",
            "/etc/journeyman/tls",
        )
    )

    TLS_CERTIFICATE_PATH = os.environ.get(
        "JOURNEYMAN_TLS_CERTIFICATE_PATH",
        str(TLS_ROOT / "journeyman-cert.pem"),
    )

    TLS_PRIVATE_KEY_PATH = os.environ.get(
        "JOURNEYMAN_TLS_PRIVATE_KEY_PATH",
        str(TLS_ROOT / "journeyman-key.pem"),
    )

    TLS_CHAIN_PATH = os.environ.get(
        "JOURNEYMAN_TLS_CHAIN_PATH",
        "",
    )

    HTTPS_PORT = int(
        os.environ.get(
            "JOURNEYMAN_HTTPS_PORT",
            "443",
        )
    )

    REPOSITORY_ROOT = Path(
        os.environ.get(
            "JOURNEYMAN_REPOSITORY_ROOT",
            "/var/lib/journeyman/repos",
        )
    )

    RUNNER_ARTIFACT_ROOT = Path(
        os.environ.get(
            "JOURNEYMAN_RUNNER_ARTIFACT_ROOT",
            "/var/lib/journeyman/runner-artifacts",
        )
    )

    LOG_ROOT = Path(
        os.environ.get(
            "JOURNEYMAN_LOG_ROOT",
            "/var/log/journeyman",
        )
    )

    APPLICATION_ENVIRONMENT_PATH = os.environ.get(
        "JOURNEYMAN_APPLICATION_ENVIRONMENT_PATH",
        str(Path(os.environ.get("VIRTUAL_ENV", os.sys.prefix))),
    )

    MANAGED_ENVIRONMENT_ROOT = os.environ.get(
        "JOURNEYMAN_MANAGED_ENVIRONMENT_ROOT",
        "/opt/journeyman/environments",
    )

    ENVIRONMENT_PYTHON_INTERPRETERS = os.environ.get(
        "JOURNEYMAN_ENVIRONMENT_PYTHONS",
        os.sys.executable,
    )

    JOB_TIMEOUT_SECONDS = int(
        os.environ.get(
            "JOURNEYMAN_JOB_TIMEOUT_SECONDS",
            "3600",
        )
    )

    CANCELLATION_STALE_SECONDS = max(
        60,
        int(os.environ.get("JOURNEYMAN_CANCELLATION_STALE_SECONDS", "300")),
    )

    JOB_RETENTION_DAYS = int(
        os.environ.get("JOURNEYMAN_JOB_RETENTION_DAYS", "180")
    )

    REACTION_RETENTION_DAYS = int(
        os.environ.get("JOURNEYMAN_REACTION_RETENTION_DAYS", "180")
    )

    INVENTORY_CACHE_RETENTION_SECONDS = int(
        os.environ.get("JOURNEYMAN_INVENTORY_CACHE_RETENTION_SECONDS", "604800")
    )

    DATA_RETENTION_PURGE_INTERVAL_SECONDS = int(
        os.environ.get("JOURNEYMAN_DATA_RETENTION_PURGE_INTERVAL_SECONDS", "3600")
    )

    # The scheduler checks frequently for changed runner dependency fingerprints,
    # but unchanged dependency sets are re-audited only once per 24 hours.
    RUNNER_RUNTIME_AUDIT_SCAN_INTERVAL_SECONDS = int(
        os.environ.get("JOURNEYMAN_RUNNER_RUNTIME_AUDIT_SCAN_INTERVAL_SECONDS", "300")
    )

    GIT_COMMAND_TIMEOUT_SECONDS = int(
        os.environ.get(
            "JOURNEYMAN_GIT_TIMEOUT_SECONDS",
            "300",
        )
    )

    NGINX_APPLY_HELPER = (
        "/usr/local/sbin/"
        "journeyman-apply-web-settings"
    )

    NGINX_APPLY_TIMEOUT_SECONDS = 30


class DevelopmentConfig(Config):
    DEBUG = True

    PROXY_FIX_X_FOR = 0
    PROXY_FIX_X_PROTO = 0
    PROXY_FIX_X_HOST = 0
    PROXY_FIX_X_PORT = 0


class ProductionConfig(Config):
    DEBUG = False

    OUTBOUND_ALLOWLIST_ENFORCED = True
    OUTBOUND_SECURE_TRANSPORT_ENFORCED = True

    # Exactly one trusted reverse proxy: local Nginx.
    PROXY_FIX_X_FOR = 1
    PROXY_FIX_X_PROTO = 1
    PROXY_FIX_X_HOST = 1
    PROXY_FIX_X_PORT = 1

    PREFERRED_URL_SCHEME = "https"

    # __Host- prevents Domain scoping and requires Secure + Path=/,
    # binding the authentication cookie to this host.
    SESSION_COOKIE_NAME = "__Host-journeyman_session"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_PATH = "/"
    SESSION_COOKIE_DOMAIN = None
