from pathlib import Path

import pytest

from app.auth import _safe_next_url
from app.config import ProductionConfig


pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "app" / "templates"
STATIC_ROOT = ROOT / "app" / "static"


def _frontend_text():
    parts = []
    for root in (TEMPLATE_ROOT, STATIC_ROOT):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".html", ".js", ".css"}:
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_production_session_cookie_is_host_scoped_and_hardened():
    assert ProductionConfig.SESSION_COOKIE_NAME.startswith("__Host-")
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE in {"Lax", "Strict"}
    assert ProductionConfig.SESSION_COOKIE_PATH == "/"
    assert ProductionConfig.SESSION_COOKIE_DOMAIN is None


def test_application_emits_browser_security_headers(client):
    response = client.get(
        "/",
        headers={"X-Test-Username": "admin"},
        base_url="https://journeyman.example.com",
    )
    assert response.status_code == 200

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]

    csp = response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp


def test_managed_nginx_emits_hsts_for_tls_responses():
    text = (ROOT / "deployment" / "journeyman_apply_web_settings.py").read_text(
        encoding="utf-8"
    )
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains" always' in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text


def test_login_redirect_rejects_external_destinations(app):
    with app.test_request_context("/"):
        assert _safe_next_url("/jobs") == "/jobs"
        assert _safe_next_url("https://evil.example/") == "/"
        assert _safe_next_url("//evil.example/") == "/"
        assert _safe_next_url("javascript:alert(1)") == "/"


def test_frontend_does_not_use_postmessage_jsonp_or_obsolete_plugin_technologies():
    text = _frontend_text().lower()
    forbidden = (
        "postmessage(",
        "callback=?",
        "jsonp",
        "<applet",
        "<object",
        "<embed",
        "activexobject",
        "silverlight",
        "shockwave",
    )
    for marker in forbidden:
        assert marker not in text


def test_client_assets_are_local_not_third_party_dependencies():
    for path in TEMPLATE_ROOT.rglob("*.html"):
        text = path.read_text(encoding="utf-8").lower()
        # External URLs in placeholders/help text are fine. Executable/style
        # resource tags must be served from Journeyman itself.
        for marker in ("<script", "<link"):
            for line in text.splitlines():
                if marker in line and ("src=" in line or "href=" in line):
                    assert "src=\"http://" not in line
                    assert "src=\"https://" not in line
                    assert "href=\"http://" not in line
                    assert "href=\"https://" not in line


def test_templates_do_not_disable_jinja_autoescaping_with_safe_filter():
    offenders = []
    for path in TEMPLATE_ROOT.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if "|safe" in text or "| safe" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
