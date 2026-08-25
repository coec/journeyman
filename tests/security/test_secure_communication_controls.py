import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import ssl

import pytest

from app.services.directory_settings import (
    DirectorySettingsValidationError,
    validate_directory_settings,
)

pytestmark = pytest.mark.security


def _load_remote_runner():
    path = Path(__file__).resolve().parents[2] / "bin" / "journeyman-remote-runner"
    loader = SourceFileLoader("journeyman_remote_runner_transport_security", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_nginx_allows_only_tls_12_and_13():
    source = (Path(__file__).resolve().parents[2] / "deployment" / "journeyman_apply_web_settings.py").read_text(encoding="utf-8")
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in source
    assert "TLSv1.0" not in source
    assert "TLSv1.1" not in source


def test_remote_runner_requires_https_control_plane_url(tmp_path):
    runner = _load_remote_runner()

    with pytest.raises(RuntimeError, match="must use https://"):
        runner.register_remote_runner(
            "http://journeyman.example",
            "one-time-token",
            config_path=tmp_path / "runner.env",
        )

    with pytest.raises(RuntimeError, match="must not contain embedded credentials"):
        runner._validated_https_url("https://user:pass@journeyman.example")


def test_remote_runner_ssl_context_requires_certificate_validation():
    runner = _load_remote_runner()
    context = runner._ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_directory_validation_rejects_plain_ldap(app):
    values = {
        "enabled": True,
        "base_dn": "DC=example,DC=com",
        "user_search_base": "OU=Users,DC=example,DC=com",
        "group_search_base": "OU=Groups,DC=example,DC=com",
        "bind_username": "svc_journeyman@example.com",
        "bind_password": "secret",
        "ca_certificate_path": "/etc/pki/ca-trust/source/anchors/ad-ca.pem",
        "connect_timeout_seconds": "3",
        "operation_timeout_seconds": "10",
        "administrator_group_name": "Journeyman Admins",
        "user_group_name": "Journeyman Users",
        "include_nested_groups": True,
        "servers": [
            {"host": "dc1.example.com", "port": "636", "use_ssl": False, "enabled": True},
            {"host": "dc2.example.com", "port": "636", "use_ssl": True, "enabled": True},
        ],
    }
    with app.app_context():
        with pytest.raises(DirectorySettingsValidationError, match="must use LDAPS"):
            validate_directory_settings(values)
