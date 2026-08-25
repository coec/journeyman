from pathlib import Path

import pytest

from app import db
from app.models import SystemSetting
from app.models.system_setting import (
    APPLY_STATUS_PENDING,
)
from app.services.system_settings import (
    SystemSettingsValidationError,
    normalize_public_fqdn,
    validate_system_settings,
)


def identity_headers(username):
    return {
        "X-Test-Username": username,
    }


def test_public_fqdn_is_normalized(app):
    with app.app_context():
        assert normalize_public_fqdn(
            "JOURNEYMAN.EXAMPLE.COM."
        ) == (
            "journeyman.example.com"
        )


def test_tls_paths_must_remain_inside_tls_root(
    app,
):
    with app.app_context():
        with pytest.raises(
            SystemSettingsValidationError
        ):
            validate_system_settings(
                {
                    "public_fqdn": (
                        "journeyman."
                        "example.com"
                    ),
                    "https_port": "443",
                    "tls_certificate_path": (
                        "/etc/shadow"
                    ),
                    "tls_private_key_path": (
                        "/etc/passwd"
                    ),
                    "tls_chain_path": "",
                    "redirect_http_to_https": True,
                }
            )


def test_non_admin_cannot_view_settings(
    client,
):
    response = client.get(
        "/settings",
        headers=identity_headers(
            "alice"
        ),
    )

    assert response.status_code == 403


def test_admin_can_save_system_settings(
    app,
    client,
):
    tls_root = Path(
        app.config["TLS_ROOT"]
    )

    response = client.post(
        "/settings",
        data={
            "public_fqdn": (
                "journeyman."
                "example.com"
            ),
            "https_port": "8443",
            "tls_certificate_path": str(
                tls_root
                / "journeyman-cert.pem"
            ),
            "tls_private_key_path": str(
                tls_root
                / "journeyman-key.pem"
            ),
            "tls_chain_path": str(
                tls_root
                / "journeyman-chain.pem"
            ),
            "redirect_http_to_https": "on",
        },
        headers=identity_headers(
            "admin"
        ),
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(
            SystemSetting,
            1,
        )

        assert settings is not None

        assert settings.public_fqdn == (
            "journeyman."
            "example.com"
        )

        assert settings.https_port == 8443

        assert settings.redirect_http_to_https

        assert settings.apply_status == (
            APPLY_STATUS_PENDING
        )

        assert settings.updated_by == "admin"
