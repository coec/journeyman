"""Regression tests for RPM-reset shebangs and app venv selection."""

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bin" / "_journeyman_app_bootstrap.py"
ENTRY_POINTS = (
    "journeyman-runner",
    "journeyman-environment-builder",
    "journeyman-runner-admin",
    "journeyman-service-coordinator",
)


def bootstrap_module():
    spec = importlib.util.spec_from_file_location("jm_app_bootstrap_test", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entrypoints_bootstrap_before_loading_application_dependencies():
    for name in ENTRY_POINTS:
        source = (ROOT / "bin" / name).read_text(encoding="utf-8")
        assert "ensure_application_venv()" in source
        for dependency_import in ("from app import", "from flask import", "from sqlalchemy import"):
            if dependency_import in source:
                assert source.index("ensure_application_venv()") < source.index(dependency_import)


def test_venv_location_is_fixed(monkeypatch):
    bootstrap = bootstrap_module()
    monkeypatch.delenv("JOURNEYMAN_VENV", raising=False)
    assert bootstrap.application_venv() == Path("/opt/journeyman/venv")
    monkeypatch.setenv("JOURNEYMAN_VENV", "/opt/journeyman/venv314")
    with pytest.raises(SystemExit, match="must be /opt/journeyman/venv"):
        bootstrap.application_venv()


def test_reexecs_with_script_path_and_arguments(tmp_path, monkeypatch):
    bootstrap = bootstrap_module()
    venv = tmp_path / "venv"
    executable = venv / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.delenv("JOURNEYMAN_VENV", raising=False)
    monkeypatch.setattr(bootstrap, "APPLICATION_VENV", venv)
    monkeypatch.setattr(bootstrap.sys, "prefix", "/usr")
    monkeypatch.setattr(bootstrap.sys, "argv", ["/opt/journeyman/bin/journeyman-runner-admin", "guard-target"])
    called = []
    monkeypatch.setattr(bootstrap.os, "execv", lambda exe, args: called.append((exe, args)))
    bootstrap.ensure_application_venv()
    assert called == [
        (str(executable), [str(executable), "/opt/journeyman/bin/journeyman-runner-admin", "guard-target"])
    ]


def test_no_reexec_when_already_in_application_venv(tmp_path, monkeypatch):
    bootstrap = bootstrap_module()
    executable = tmp_path / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.delenv("JOURNEYMAN_VENV", raising=False)
    monkeypatch.setattr(bootstrap, "APPLICATION_VENV", tmp_path)
    monkeypatch.setattr(bootstrap.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(bootstrap.os, "execv", lambda *_: pytest.fail("unnecessary re-exec"))
    bootstrap.ensure_application_venv()
