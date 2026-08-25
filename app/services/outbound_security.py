"""Central policy for Journeyman-initiated outbound network destinations."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from flask import current_app, has_app_context


class OutboundSecurityError(ValueError):
    """Raised when an outbound destination violates Journeyman policy."""


MAX_OUTBOUND_URL_LENGTH = 2048
MAX_OUTBOUND_HOSTNAME_LENGTH = 253

MAX_OUTBOUND_HEADER_VALUE_LENGTH = 8192


def validate_http_header_value(value, *, purpose="HTTP header"):
    text = str(value or "")
    if "\r" in text or "\n" in text:
        raise OutboundSecurityError(
            "{} must not contain carriage returns or newlines.".format(purpose)
        )
    if len(text.encode("utf-8")) > MAX_OUTBOUND_HEADER_VALUE_LENGTH:
        raise OutboundSecurityError(
            "{} exceeds the maximum supported length.".format(purpose)
        )
    return text


def _validate_outbound_text_bounds(value, purpose):
    if len(value) > MAX_OUTBOUND_URL_LENGTH:
        raise OutboundSecurityError(
            "{} URL exceeds the maximum supported length of {} characters."
            .format(purpose, MAX_OUTBOUND_URL_LENGTH)
        )


def _configured_allowed_hosts():
    if not has_app_context():
        return ()
    return tuple(current_app.config.get("OUTBOUND_ALLOWED_HOSTS", ()))


def _allowlist_enforced():
    if not has_app_context():
        return False
    return bool(current_app.config.get("OUTBOUND_ALLOWLIST_ENFORCED", False))


def secure_transport_enforced():
    if not has_app_context():
        return False
    return bool(current_app.config.get("OUTBOUND_SECURE_TRANSPORT_ENFORCED", False))


def _normalise_allowed_entry(value):
    return str(value or "").strip().lower().rstrip(".")


def _host_matches(hostname, port, entry):
    entry = _normalise_allowed_entry(entry)
    if not entry or entry == "*":
        return False

    wanted_port = None
    pattern = entry
    if entry.count(":") == 1:
        candidate, port_text = entry.rsplit(":", 1)
        if port_text.isdigit():
            pattern = candidate
            wanted_port = int(port_text)

    if wanted_port is not None and port != wanted_port:
        return False

    if pattern.startswith("*."):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname != suffix[1:]
    return hostname == pattern


def _reject_local_special_address(hostname):
    """Reject loopback/link-local/unspecified/multicast literal IP targets."""

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return

    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        raise OutboundSecurityError(
            "Outbound destination uses a prohibited local/special IP address."
        )


def validate_outbound_url(url, *, purpose="outbound service", require_https=True):
    """Validate scheme, credentials and sysadmin-owned destination allowlist."""

    value = str(url or "").strip()
    _validate_outbound_text_bounds(value, purpose)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise OutboundSecurityError("{} URL is invalid.".format(purpose)) from exc

    if require_https and secure_transport_enforced() and parsed.scheme.lower() != "https":
        raise OutboundSecurityError("{} URL must use https://.".format(purpose))
    if require_https and not secure_transport_enforced() and parsed.scheme.lower() not in {"http", "https"}:
        raise OutboundSecurityError("{} URL must use http:// or https://.".format(purpose))
    if not require_https and parsed.scheme.lower() not in {"http", "https"}:
        raise OutboundSecurityError("{} URL must use http:// or https://.".format(purpose))
    if not parsed.hostname:
        raise OutboundSecurityError("{} URL must contain a hostname.".format(purpose))
    if parsed.username or parsed.password:
        raise OutboundSecurityError(
            "{} URL must not contain embedded credentials.".format(purpose)
        )
    if parsed.fragment:
        raise OutboundSecurityError("{} URL must not contain a fragment.".format(purpose))

    hostname = parsed.hostname.lower().rstrip(".")
    _reject_local_special_address(hostname)
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise OutboundSecurityError(
            "{} URL contains an invalid port.".format(purpose)
        ) from exc

    if _allowlist_enforced():
        allowed = _configured_allowed_hosts()
        if not any(_host_matches(hostname, port, entry) for entry in allowed):
            raise OutboundSecurityError(
                "{} host {!r} is not in JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS."
                .format(purpose, hostname)
            )
    return value


_SCP_STYLE_GIT_RE = re.compile(
    r"^(?:(?P<username>[A-Za-z0-9._-]+)@)?"
    r"(?P<hostname>[^/:@\s]+):"
    r"(?P<path>.+)$"
)


def _validate_allowed_destination(hostname, port, purpose):
    hostname = str(hostname or "").strip().lower().rstrip(".")
    if len(hostname) > MAX_OUTBOUND_HOSTNAME_LENGTH:
        raise OutboundSecurityError(
            "{} hostname exceeds the maximum supported length.".format(purpose)
        )
    if not hostname:
        raise OutboundSecurityError(
            "{} URL must contain a hostname.".format(purpose)
        )

    _reject_local_special_address(hostname)

    if _allowlist_enforced():
        allowed = _configured_allowed_hosts()
        if not any(
            _host_matches(hostname, port, entry)
            for entry in allowed
        ):
            raise OutboundSecurityError(
                "{} host {!r} is not in "
                "JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS."
                .format(purpose, hostname)
            )

    return hostname


def validate_outbound_destination(hostname, port, *, purpose="outbound service"):
    """Validate a host/port destination against the configured outbound allowlist."""
    return _validate_allowed_destination(hostname, int(port), purpose)


def validate_repository_url(url):
    """Validate an HTTPS or SSH Git repository destination.

    Git supports both ordinary URLs and the SCP-like SSH shorthand
    ``user@host:path``. Production transport policy requires HTTPS for HTTP
    repositories, but SSH is an independently encrypted and authenticated
    transport and is therefore permitted. Both forms remain subject to the
    sysadmin-owned outbound host allowlist.
    """

    value = str(url or "").strip()
    _validate_outbound_text_bounds(value, "Repository")
    if not value:
        raise OutboundSecurityError(
            "Repository URL is required."
        )

    lowered = value.lower()

    if lowered.startswith(("http://", "https://")):
        return validate_outbound_url(
            value,
            purpose="Repository",
            require_https=True,
        )

    if lowered.startswith("ssh://"):
        try:
            parsed = urlsplit(value)
            port = parsed.port or 22
        except ValueError as exc:
            raise OutboundSecurityError(
                "Repository SSH URL is invalid."
            ) from exc

        if parsed.scheme.lower() != "ssh" or not parsed.hostname:
            raise OutboundSecurityError(
                "Repository SSH URL must contain a hostname."
            )
        if parsed.password:
            raise OutboundSecurityError(
                "Repository SSH URL must not contain a password."
            )
        if parsed.fragment:
            raise OutboundSecurityError(
                "Repository SSH URL must not contain a fragment."
            )
        if not str(parsed.path or "").strip("/"):
            raise OutboundSecurityError(
                "Repository SSH URL must contain a repository path."
            )

        _validate_allowed_destination(
            parsed.hostname,
            port,
            "Repository",
        )
        return value

    match = _SCP_STYLE_GIT_RE.fullmatch(value)
    if match:
        path = str(match.group("path") or "").strip()
        if not path:
            raise OutboundSecurityError(
                "Repository SSH URL must contain a repository path."
            )
        _validate_allowed_destination(
            match.group("hostname"),
            22,
            "Repository",
        )
        return value

    raise OutboundSecurityError(
        "Repository URL must use https://, ssh://, "
        "or SSH form user@host:path."
    )


def validate_database_transport(database_uri):
    """Require verified TLS for non-local PostgreSQL connections."""
    value = str(database_uri or "").strip()
    if not value.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
        return value
    from sqlalchemy.engine import make_url
    try:
        parsed = make_url(value)
    except Exception as exc:
        raise OutboundSecurityError("PostgreSQL database URI is invalid.") from exc
    host = str(parsed.host or "").strip().lower()
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return value
    sslmode = str(parsed.query.get("sslmode", "")).strip().lower()
    if sslmode != "verify-full":
        raise OutboundSecurityError(
            "Remote PostgreSQL connections must use sslmode=verify-full."
        )
    return value
