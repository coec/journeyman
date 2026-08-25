from pathlib import Path
import inspect

import pytest

from app import create_app
from app.config import ProductionConfig


pytestmark = pytest.mark.security


ROOT = Path(__file__).resolve().parents[2]


def test_production_configuration_disables_debug_and_hardens_session_cookie():
    assert ProductionConfig.DEBUG is False
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"


def test_packaged_local_services_require_yaml_configuration():
    service_names = (
        "journeyman.service",
        "journeyman-web.service",
        "journeyman-runner.service",
        "journeyman-scheduler.service",
        "journeyman-environment-builder.service",
    )

    for service_name in service_names:
        text = (ROOT / "deploy" / "systemd" / service_name).read_text(
            encoding="utf-8"
        )
        assert "Environment=JOURNEYMAN_CONFIG=/etc/journeyman/journeyman.yml" in text
        assert "EnvironmentFile=" not in text

    web = (
        ROOT / "deploy" / "systemd" / "journeyman-web.service"
    ).read_text(
        encoding="utf-8"
    )
    assert 'JOURNEYMAN_CONFIG=app.config.ProductionConfig' not in web
    assert "--bind 127.0.0.1:5000" in web
    assert "NoNewPrivileges=true" in web


def test_development_entry_point_does_not_force_debug_mode():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "debug=True" not in text
    assert 'app.config.get("DEBUG", False)' in text


def test_trace_method_is_not_supported(client):
    response = client.open(
        "/",
        method="TRACE",
        headers={"X-Test-Username": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 405


def test_application_response_headers_do_not_advertise_backend_framework_versions(client):
    response = client.get("/", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200

    header_text = "\n".join(
        "{}: {}".format(name, value)
        for name, value in response.headers.items()
    ).lower()

    for marker in ("werkzeug", "flask", "gunicorn", "python/"):
        assert marker not in header_text


def test_production_startup_guard_rejects_documented_secret_placeholder():
    source = inspect.getsource(create_app)
    assert '"CHANGE_ME"' in source
    assert '"development-only-change-me"' in source
