"""ASVS 13.1.3 evidence for bounded backend failure handling."""

import subprocess
from types import SimpleNamespace

import pytest
import re

from app.services.git import GitError, _run as run_git
from app.services.runner_artifacts import RunnerArtifactError


pytestmark = pytest.mark.security


def test_repository_git_timeout_is_bounded(app, monkeypatch):
    app.config["GIT_COMMAND_TIMEOUT_SECONDS"] = 7
    captured = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=kwargs["timeout"])

    monkeypatch.setattr("app.services.git.subprocess.run", fake_run)

    with app.app_context():
        with pytest.raises(GitError, match="7 second timeout"):
            run_git(["git", "fetch"])

    assert captured["timeout"] == 7


def test_runner_artifact_git_timeout_is_bounded(app, monkeypatch, tmp_path):
    from app.services import runner_artifacts

    app.config["GIT_COMMAND_TIMEOUT_SECONDS"] = 9
    captured = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=kwargs["timeout"])

    monkeypatch.setattr(runner_artifacts.subprocess, "run", fake_run)

    with app.app_context():
        with pytest.raises(RunnerArtifactError, match="9 second timeout"):
            runner_artifacts._run_git(["status"], tmp_path)

    assert captured["timeout"] == 9


def test_backend_policy_keeps_bounded_timeouts_on_known_call_sites():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    expected = {
        "app/services/foreman_inventory.py": ("timeout=timeout", "finally:"),
        "app/services/zabbix_inventory.py": ("timeout=timeout", "with response_context as response"),
        "app/services/static_inventory.py": ("timeout=timeout", "finally:"),
        "app/services/execution_target_hosts.py": ("timeout=timeout_seconds", "TemporaryDirectory"),
        "app/services/environments.py": ("timeout=timeout",),
        "app/services/environment_build_settings.py": ("timeout=15", "with opener.open"),
        "app/services/system_settings_apply.py": ("timeout=timeout",),
        "app/services/system_status.py": ("timeout=3",),
        "app/services/directory.py": ("connect_timeout=", "receive_timeout=", "connection.unbind()"),
    }

    for relative_path, markers in expected.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, "{} missing {!r}".format(relative_path, marker)


def test_backend_policy_defines_no_unbounded_retry_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    policy = (
        root / "docs" / "security" / "BACKEND_FAILURE_POLICY.md"
    ).read_text(encoding="utf-8")

    assert "retry budget is **zero**" in policy
    assert "one attempt against each enabled directory server" in policy
    assert "finite connection/execution timeout" in policy
    assert re.search(r"bounded\s+backoff with jitter", policy)
