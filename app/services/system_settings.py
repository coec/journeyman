import hashlib
import ipaddress
import json
import re
from pathlib import Path

from flask import current_app

from app import db
from app.models import SystemSetting
from app.models.system_setting import (
    APPLY_STATUS_NEVER_APPLIED,
    APPLY_STATUS_PENDING,
    SYSTEM_SETTING_ID,
)


FQDN_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class SystemSettingsValidationError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)

        super().__init__(
            " ".join(self.errors)
        )


def _clean(value):
    return str(value or "").strip()


def normalize_public_fqdn(value):
    value = _clean(value).lower().rstrip(".")

    if not value:
        raise ValueError(
            "Public FQDN is required."
        )

    if len(value) > 253:
        raise ValueError(
            "Public FQDN cannot exceed 253 characters."
        )

    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError(
            "Public FQDN must be a hostname, not an IP address."
        )

    labels = value.split(".")

    if len(labels) < 2:
        raise ValueError(
            "Public FQDN must contain at least one dot."
        )

    for label in labels:
        if not FQDN_LABEL_PATTERN.fullmatch(label):
            raise ValueError(
                "Public FQDN contains an invalid hostname label."
            )

    return value


def normalize_tls_path(
    value,
    *,
    field_label,
    required,
):
    value = _clean(value)

    if not value:
        if required:
            raise ValueError(
                "{} is required.".format(
                    field_label
                )
            )

        return ""

    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(
            "{} contains invalid characters."
            .format(field_label)
        )

    supplied_path = Path(value)

    if not supplied_path.is_absolute():
        raise ValueError(
            "{} must be an absolute path."
            .format(field_label)
        )

    tls_root = Path(
        current_app.config["TLS_ROOT"]
    ).resolve()

    resolved_path = supplied_path.resolve(
        strict=False
    )

    if (
        resolved_path == tls_root
        or tls_root not in resolved_path.parents
    ):
        raise ValueError(
            "{} must be beneath {}."
            .format(
                field_label,
                tls_root,
            )
        )

    return str(resolved_path)


def default_system_settings_values():
    return {
        "public_fqdn": current_app.config[
            "PUBLIC_FQDN"
        ],
        "https_port": current_app.config[
            "HTTPS_PORT"
        ],
        "tls_certificate_path": (
            current_app.config[
                "TLS_CERTIFICATE_PATH"
            ]
        ),
        "tls_private_key_path": (
            current_app.config[
                "TLS_PRIVATE_KEY_PATH"
            ]
        ),
        "tls_chain_path": current_app.config[
            "TLS_CHAIN_PATH"
        ],
        "redirect_http_to_https": True,
    }


def system_settings_form_data(form):
    return {
        "public_fqdn": _clean(
            form.get("public_fqdn")
        ),
        "https_port": _clean(
            form.get("https_port")
        ),
        "tls_certificate_path": _clean(
            form.get("tls_certificate_path")
        ),
        "tls_private_key_path": _clean(
            form.get("tls_private_key_path")
        ),
        "tls_chain_path": _clean(
            form.get("tls_chain_path")
        ),
        "redirect_http_to_https": (
            form.get("redirect_http_to_https")
            == "on"
        ),
    }


def settings_to_form_data(settings):
    return {
        "public_fqdn": settings.public_fqdn,
        "https_port": str(
            settings.https_port
        ),
        "tls_certificate_path": (
            settings.tls_certificate_path
        ),
        "tls_private_key_path": (
            settings.tls_private_key_path
        ),
        "tls_chain_path": (
            settings.tls_chain_path
        ),
        "redirect_http_to_https": (
            settings.redirect_http_to_https
        ),
    }


def validate_system_settings(values):
    errors = []
    normalized = {}

    try:
        normalized["public_fqdn"] = (
            normalize_public_fqdn(
                values.get("public_fqdn")
            )
        )
    except ValueError as exc:
        errors.append(str(exc))

    raw_port = _clean(
        values.get("https_port")
    )

    try:
        https_port = int(raw_port)
    except (TypeError, ValueError):
        errors.append(
            "HTTPS port must be a number."
        )
    else:
        if not 1 <= https_port <= 65535:
            errors.append(
                "HTTPS port must be between 1 and 65535."
            )
        else:
            normalized["https_port"] = (
                https_port
            )

    path_fields = (
        (
            "tls_certificate_path",
            "Certificate path",
            True,
        ),
        (
            "tls_private_key_path",
            "Private-key path",
            True,
        ),
        (
            "tls_chain_path",
            "Certificate-chain path",
            False,
        ),
    )

    for field_name, field_label, required in path_fields:
        try:
            normalized[field_name] = (
                normalize_tls_path(
                    values.get(field_name),
                    field_label=field_label,
                    required=required,
                )
            )
        except ValueError as exc:
            errors.append(str(exc))

    normalized["redirect_http_to_https"] = bool(
        values.get("redirect_http_to_https")
    )

    if errors:
        raise SystemSettingsValidationError(
            errors
        )

    return normalized


def configuration_payload(settings):
    return {
        "public_fqdn": settings.public_fqdn,
        "https_port": settings.https_port,
        "tls_certificate_path": (
            settings.tls_certificate_path
        ),
        "tls_private_key_path": (
            settings.tls_private_key_path
        ),
        "tls_chain_path": (
            settings.tls_chain_path
        ),
        "redirect_http_to_https": (
            settings.redirect_http_to_https
        ),
    }


def configuration_sha256(settings):
    serialized = json.dumps(
        configuration_payload(settings),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def get_or_create_system_settings():
    settings = db.session.get(
        SystemSetting,
        SYSTEM_SETTING_ID,
    )

    if settings is not None:
        return settings

    defaults = validate_system_settings(
        default_system_settings_values()
    )

    settings = SystemSetting(
        id=SYSTEM_SETTING_ID,
        apply_status=(
            APPLY_STATUS_NEVER_APPLIED
        ),
        apply_message=(
            "Settings have not yet been "
            "applied to Nginx."
        ),
        updated_by="system",
        **defaults,
    )

    db.session.add(settings)
    db.session.commit()

    return settings


def update_system_settings(
    settings,
    values,
    *,
    updated_by,
):
    old_digest = configuration_sha256(
        settings
    )

    settings.public_fqdn = values[
        "public_fqdn"
    ]

    settings.https_port = values[
        "https_port"
    ]

    settings.tls_certificate_path = values[
        "tls_certificate_path"
    ]

    settings.tls_private_key_path = values[
        "tls_private_key_path"
    ]

    settings.tls_chain_path = values[
        "tls_chain_path"
    ]

    settings.redirect_http_to_https = values[
        "redirect_http_to_https"
    ]

    settings.updated_by = _clean(
        updated_by
    ) or "system"

    new_digest = configuration_sha256(
        settings
    )

    if new_digest != old_digest:
        settings.apply_status = (
            APPLY_STATUS_PENDING
        )

        settings.apply_message = (
            "Settings were saved and are "
            "awaiting Nginx application."
        )

    db.session.commit()

    return settings
