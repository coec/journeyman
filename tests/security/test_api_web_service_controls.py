"""Cross-cutting evidence for ASVS V4 API and Web Service."""

from pathlib import Path

import pytest

from app.config import ProductionConfig


pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]


def test_runner_api_requires_json_for_json_body_endpoints(client):
    response = client.post(
        "/api/runners/register",
        data="token=not-json",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 415
    assert response.is_json
    assert response.get_json()["error"] == "Content-Type must be application/json."


def test_runner_api_rejects_non_object_json_payloads(client):
    response = client.post(
        "/api/runners/register",
        json=["not", "an", "object"],
    )
    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"] == "Request body must be a JSON object."


def test_api_response_content_type_and_length_match_body(client):
    response = client.post(
        "/api/runners/register",
        json={"token": "invalid"},
    )
    assert response.status_code == 403
    assert response.content_type == "application/json"
    assert response.content_length == len(response.get_data())


def test_runner_api_blocks_unsupported_http_methods(client):
    for method in ("GET", "PUT", "PATCH", "DELETE", "TRACE"):
        response = client.open(
            "/api/runners/register",
            method=method,
            follow_redirects=False,
        )
        assert response.status_code == 405, method


def test_managed_http_listener_does_not_redirect_api_requests_to_https():
    text = (ROOT / "deployment" / "journeyman_apply_web_settings.py").read_text(
        encoding="utf-8"
    )
    assert "location ^~ /api/" in text
    assert "return 426;" in text
    assert "location /" in text
    assert "return 301 https://{fqdn}{port}$request_uri;" in text


def test_proxy_headers_are_owned_by_single_loopback_reverse_proxy():
    assert ProductionConfig.PROXY_FIX_X_FOR == 1
    assert ProductionConfig.PROXY_FIX_X_PROTO == 1
    assert ProductionConfig.PROXY_FIX_X_HOST == 1
    assert ProductionConfig.PROXY_FIX_X_PORT == 1

    service = (
        ROOT / "deploy" / "systemd" / "journeyman-web.service"
    ).read_text(
        encoding="utf-8"
    )
    nginx = (ROOT / "deployment" / "journeyman_apply_web_settings.py").read_text(
        encoding="utf-8"
    )

    assert "--bind 127.0.0.1:5000" in service
    assert "proxy_set_header X-Real-IP $remote_addr;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-Port $server_port;" in nginx
    # ProxyFix trusts the right-most address added by the single local proxy.
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in nginx
