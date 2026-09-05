import os
from types import SimpleNamespace

import pytest

from app.services.outbound_security import (
    OutboundSecurityError,
    validate_outbound_url,
    validate_repository_url,
)

pytestmark = pytest.mark.security


def _enforce(app, hosts):
    app.config["OUTBOUND_ALLOWLIST_ENFORCED"] = True
    app.config["OUTBOUND_SECURE_TRANSPORT_ENFORCED"] = True
    app.config["OUTBOUND_ALLOWED_HOSTS"] = tuple(hosts)


def test_production_outbound_url_requires_https_and_allowlisted_host(app):
    with app.app_context():
        _enforce(app, ("git.example.test", "*.infra.example.test:8443"))
        assert validate_outbound_url("https://git.example.test/repo.git")
        assert validate_outbound_url("https://zabbix.infra.example.test:8443/api")
        with pytest.raises(OutboundSecurityError, match="must use https"):
            validate_outbound_url("http://git.example.test/repo.git")
        with pytest.raises(OutboundSecurityError, match="not in JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS"):
            validate_outbound_url("https://evil.example.test/repo.git")


def test_outbound_url_rejects_embedded_credentials_and_special_ip(app):
    with app.app_context():
        _enforce(app, ("127.0.0.1", "git.example.test"))
        with pytest.raises(OutboundSecurityError, match="embedded credentials"):
            validate_outbound_url("https://user:pass@git.example.test/repo")
        with pytest.raises(OutboundSecurityError, match="prohibited local/special"):
            validate_outbound_url("https://127.0.0.1/service")


def test_zabbix_tls_verification_cannot_be_disabled(app):
    from app.services.zabbix_inventory import ZabbixInventoryError, _ssl_context
    with app.app_context():
        _enforce(app, ("zabbix.example.test",))
        with pytest.raises(ZabbixInventoryError, match="cannot be disabled"):
            _ssl_context(False)


def test_git_runtime_disables_http_redirects(app, monkeypatch, tmp_path):
    from app.services import git as git_service
    captured = []
    monkeypatch.setattr(git_service, "_run", lambda args, cwd=None, env=None: captured.append((args, env)) or ("a"*40 + "\x1fmsg\x1fauthor\x1f2026-08-11T00:00:00+00:00" if args[1:3] == ["log", "-1"] else ""))
    with app.app_context():
        _enforce(app, ("git.example.test",))
        repo = SimpleNamespace(id=991, url="https://git.example.test/repo.git", default_branch="main")
        git_service.sync_repository(repo, tmp_path, token="test-token")
    clone_env = captured[0][1]
    assert clone_env["GIT_CONFIG_KEY_0"] == "http.followRedirects"
    assert clone_env["GIT_CONFIG_VALUE_0"] == "false"



def test_git_repository_urls_accept_https_and_ssh_with_same_allowlist(app):
    with app.app_context():
        _enforce(
            app,
            (
                "git.example.test",
                "ssh-only.example.test:22",
            ),
        )

        assert validate_repository_url(
            "https://git.example.test/team/repo.git"
        )
        assert validate_repository_url(
            "git@git.example.test:team/repo.git"
        )
        assert validate_repository_url(
            "ssh://git@ssh-only.example.test/team/repo.git"
        )

        with pytest.raises(
            OutboundSecurityError,
            match="must use https",
        ):
            validate_repository_url(
                "http://git.example.test/team/repo.git"
            )

        with pytest.raises(
            OutboundSecurityError,
            match="not in JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
        ):
            validate_repository_url(
                "git@evil.example.test:team/repo.git"
            )


def test_git_runtime_accepts_scp_style_ssh_repository(app, monkeypatch, tmp_path):
    from app.services import git as git_service

    captured = []

    def fake_run(args, cwd=None, env=None):
        captured.append(args)
        if args[1:3] == ["log", "-1"]:
            return (
                "a" * 40
                + "\x1fmsg\x1fauthor\x1f"
                + "2026-08-11T00:00:00+00:00"
            )
        return ""

    monkeypatch.setattr(
        git_service,
        "_run",
        fake_run,
    )

    with app.app_context():
        _enforce(
            app,
            ("git.example.test",),
        )
        repo = SimpleNamespace(
            id=992,
            url="git@git.example.test:team/repo.git",
            default_branch="main",
        )
        git_service.sync_repository(
            repo,
            tmp_path,
        )

    assert captured
    assert captured[0][0:2] == ["git", "clone"]
    assert (
        "git@git.example.test:team/repo.git"
        in captured[0]
    )

def test_remote_postgresql_requires_verified_tls():
    from app.services.outbound_security import validate_database_transport
    with pytest.raises(OutboundSecurityError, match="sslmode=verify-full"):
        validate_database_transport(
            "postgresql+psycopg://user:pass@db.example.test/journeyman"
        )
    assert validate_database_transport(
        "postgresql+psycopg://user:pass@db.example.test/journeyman?sslmode=verify-full"
    )


def test_production_config_enforces_outbound_policy():
    from app.config import ProductionConfig
    assert ProductionConfig.OUTBOUND_ALLOWLIST_ENFORCED is True
    assert ProductionConfig.OUTBOUND_SECURE_TRANSPORT_ENFORCED is True


def test_outbound_url_rejects_non_numeric_port_cleanly(app):
    with app.app_context():
        _enforce(app, ("git.example.test",))
        with pytest.raises(
            OutboundSecurityError,
            match="contains an invalid port",
        ):
            validate_repository_url(
                "https://git.example.test:omts/team/repo.git"
            )


def test_notification_smtp_may_target_journeyman_itself_without_allowlist_entry(app):
    from app.services.outbound_security import validate_outbound_destination

    with app.app_context():
        _enforce(app, ("git.example.test",))
        app.config["PUBLIC_FQDN"] = "jm.local"

        assert validate_outbound_destination(
            "jm.local",
            25,
            purpose="Notification SMTP",
            allow_self=True,
        ) == "jm.local"
        assert validate_outbound_destination(
            "localhost",
            25,
            purpose="Notification SMTP",
            allow_self=True,
        ) == "localhost"
        assert validate_outbound_destination(
            "127.0.0.1",
            25,
            purpose="Notification SMTP",
            allow_self=True,
        ) == "127.0.0.1"


def test_self_destination_exception_is_opt_in(app):
    from app.services.outbound_security import validate_outbound_destination

    with app.app_context():
        _enforce(app, ("git.example.test",))
        app.config["PUBLIC_FQDN"] = "jm.local"

        with pytest.raises(
            OutboundSecurityError,
            match="not in JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
        ):
            validate_outbound_destination(
                "jm.local",
                25,
                purpose="outbound service",
            )

        with pytest.raises(
            OutboundSecurityError,
            match="prohibited local/special",
        ):
            validate_outbound_destination(
                "127.0.0.1",
                25,
                purpose="outbound service",
            )
