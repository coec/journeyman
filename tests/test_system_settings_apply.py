from app import db
from app.models import SystemSetting
from app.models.system_setting import (
    APPLY_STATUS_APPLIED,
    APPLY_STATUS_FAILED,
)
from app.services.system_settings import (
    configuration_sha256,
)
from app.services.system_settings_apply import (
    SystemSettingsApplyError,
)


def identity_headers(username):
    return {
        "X-Test-Username": username,
    }


def test_non_admin_cannot_apply_settings(
    client,
):
    response = client.post(
        "/settings/apply",
        headers=identity_headers(
            "alice"
        ),
    )

    assert response.status_code == 403


def test_admin_can_apply_settings(
    app,
    client,
    monkeypatch,
):
    def fake_apply(settings):
        return {
            "configuration_sha256": (
                configuration_sha256(
                    settings
                )
            ),
            "message": (
                "Nginx configuration was applied."
            ),
            "public_url": (
                "https://"
                + settings.public_fqdn
            ),
        }

    monkeypatch.setattr(
        "app.views.settings.apply_nginx_settings",
        fake_apply,
    )

    response = client.post(
        "/settings/apply",
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

        assert settings.apply_status == (
            APPLY_STATUS_APPLIED
        )

        assert settings.last_applied_at is not None

        assert settings.applied_config_sha256 == (
            configuration_sha256(
                settings
            )
        )


def test_failed_apply_is_recorded(
    app,
    client,
    monkeypatch,
):
    def fake_apply(settings):
        raise SystemSettingsApplyError(
            "Certificate does not cover "
            "the public FQDN."
        )

    monkeypatch.setattr(
        "app.views.settings.apply_nginx_settings",
        fake_apply,
    )

    response = client.post(
        "/settings/apply",
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

        assert settings.apply_status == (
            APPLY_STATUS_FAILED
        )

        assert (
            "Certificate does not cover"
            in settings.apply_message
        )
