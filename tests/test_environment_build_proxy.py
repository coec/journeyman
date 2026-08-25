import pytest

from app import db
from app.models import EnvironmentBuildSetting
from app.services.environment_build_settings import (
    EnvironmentBuildSettingsError,
    proxy_url_with_credentials,
    redact_proxy_secrets,
    validate,
)
from app.services.outbound_security import OutboundSecurityError, validate_outbound_url


def _proxy_values(url):
    return {
        "proxy_enabled": True,
        "proxy_url": url,
        "proxy_username": "",
        "proxy_password": "",
        "has_proxy_password": False,
        "no_proxy": "",
    }


def test_http_environment_build_proxy_is_allowed_when_secure_transport_is_enforced(app):
    app.config["OUTBOUND_SECURE_TRANSPORT_ENFORCED"] = True

    with app.app_context():
        values = validate(_proxy_values("http://proxy.example.test:3128"))

    assert values["proxy_url"] == "http://proxy.example.test:3128"


def test_secure_transport_policy_still_rejects_http_for_normal_destinations(app):
    app.config["OUTBOUND_SECURE_TRANSPORT_ENFORCED"] = True

    with app.app_context(), pytest.raises(OutboundSecurityError, match="must use https://"):
        validate_outbound_url("http://example.test/resource", purpose="Test service")


def test_proxy_url_with_credentials_accepts_http_proxy(app):
    app.config["OUTBOUND_SECURE_TRANSPORT_ENFORCED"] = True

    with app.app_context():
        settings = EnvironmentBuildSetting(
            id=1,
            proxy_enabled=True,
            proxy_url="http://proxy.example.test:3128",
        )
        db.session.add(settings)
        db.session.commit()

        assert proxy_url_with_credentials(settings) == "http://proxy.example.test:3128"


def test_redaction_never_raises_for_invalid_stored_proxy_url(app):
    app.config["OUTBOUND_SECURE_TRANSPORT_ENFORCED"] = True

    with app.app_context():
        settings = EnvironmentBuildSetting(
            id=1,
            proxy_enabled=True,
            proxy_url="not-a-valid-proxy-url",
        )
        db.session.add(settings)
        db.session.commit()

        assert redact_proxy_secrets("Python 3.14.0") == "Python 3.14.0"
