import os

import pytest

from journeyman_configuration import (
    DEFAULT_CONFIGURATION_FILE,
    configuration_path,
    load_journeyman_configuration,
)


def test_load_journeyman_configuration_exports_values_and_yaml_overrides_environment(tmp_path, monkeypatch):
    config_file = tmp_path / "journeyman.yml"
    config_file.write_text(
        """version: 1
application:
  config_class: app.config.ProductionConfig
database:
  uri: postgresql+psycopg://user:pass@db/journeyman
authentication:
  directory_admin_group_name: Tower Admins
web:
  tls_chain_path: ""
paths:
  job_root: /file/value
outbound:
  allowed_hosts:
    - git.example.com
    - proxy.example.com:8443
""",
        encoding="utf-8",
    )
    for name in (
        "JOURNEYMAN_CONFIG_CLASS",
        "JOURNEYMAN_DATABASE_URI",
        "JOURNEYMAN_DIRECTORY_ADMIN_GROUP_NAME",
        "JOURNEYMAN_TLS_CHAIN_PATH",
        "JOURNEYMAN_JOB_ROOT",
        "JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JOURNEYMAN_JOB_ROOT", "/caller/value")

    assert load_journeyman_configuration(config_file) is True
    assert os.environ["JOURNEYMAN_CONFIG_CLASS"] == "app.config.ProductionConfig"
    assert os.environ["JOURNEYMAN_DATABASE_URI"].startswith("postgresql+psycopg://")
    assert os.environ["JOURNEYMAN_DIRECTORY_ADMIN_GROUP_NAME"] == "Tower Admins"
    assert os.environ["JOURNEYMAN_TLS_CHAIN_PATH"] == ""
    assert os.environ["JOURNEYMAN_JOB_ROOT"] == "/file/value"
    assert os.environ["JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS"] == "git.example.com,proxy.example.com:8443"


def test_yaml_outbound_allowlist_replaces_stale_legacy_environment(tmp_path, monkeypatch):
    config_file = tmp_path / "journeyman.yml"
    config_file.write_text(
        """version: 1
outbound:
  allowed_hosts:
    - console.redhat.com
    - sso.redhat.com
    - proxy01.example.com
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS",
        "gitlab.example.com,old-proxy.example.com",
    )

    assert load_journeyman_configuration(config_file) is True
    assert os.environ["JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS"] == (
        "console.redhat.com,sso.redhat.com,proxy01.example.com"
    )


def test_load_journeyman_configuration_ignores_missing_default_file(tmp_path):
    assert load_journeyman_configuration(tmp_path / "missing.yml") is False


def test_load_journeyman_configuration_requires_version(tmp_path):
    config_file = tmp_path / "journeyman.yml"
    config_file.write_text("database:\n  uri: sqlite:///test.db\n", encoding="utf-8")
    with pytest.raises(ValueError, match="configuration version must be 1"):
        load_journeyman_configuration(config_file)


def test_load_journeyman_configuration_rejects_unknown_keys(tmp_path):
    config_file = tmp_path / "journeyman.yml"
    config_file.write_text("version: 1\ndatabase:\n  urii: sqlite:///test.db\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown database key"):
        load_journeyman_configuration(config_file)


def test_legacy_config_class_selector_is_translated_to_yaml_path(monkeypatch):
    monkeypatch.setenv("JOURNEYMAN_CONFIG", "app.config.ProductionConfig")
    monkeypatch.delenv("JOURNEYMAN_CONFIG_CLASS", raising=False)

    assert configuration_path() == DEFAULT_CONFIGURATION_FILE
    assert os.environ["JOURNEYMAN_CONFIG_CLASS"] == "app.config.ProductionConfig"
    assert os.environ["JOURNEYMAN_CONFIG"] == str(DEFAULT_CONFIGURATION_FILE)


def test_yaml_config_path_is_not_treated_as_legacy_selector(tmp_path, monkeypatch):
    config_file = tmp_path / "journeyman.yml"
    monkeypatch.setenv("JOURNEYMAN_CONFIG", str(config_file))
    monkeypatch.delenv("JOURNEYMAN_CONFIG_CLASS", raising=False)

    assert configuration_path() == config_file
    assert "JOURNEYMAN_CONFIG_CLASS" not in os.environ
