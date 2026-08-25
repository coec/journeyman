"""ASVS 15.3.2 evidence for outbound HTTP redirect rejection."""

import json
import os
from types import SimpleNamespace

import pytest

from app.services.foreman_inventory import resolve_foreman_inventory


pytestmark = pytest.mark.security


def test_foreman_subprocess_installs_requests_no_redirect_guard(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        environment = kwargs["env"]
        guard_dir = environment["PYTHONPATH"].split(os.pathsep, 1)[0]
        guard_path = os.path.join(guard_dir, "sitecustomize.py")

        captured["guard_dir"] = guard_dir
        captured["guard_source"] = open(
            guard_path,
            encoding="utf-8",
        ).read()

        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "_meta": {
                        "hostvars": {},
                    },
                    "all": {
                        "children": [],
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "app.services.foreman_inventory.subprocess.run",
        fake_run,
    )

    result = resolve_foreman_inventory(
        host="https://satellite.example.test",
        username="inventory",
        password="secret",
        organization="Example",
        verify_tls=True,
    )

    assert result["_meta"]["hostvars"] == {}

    source = captured["guard_source"]
    assert 'kwargs["allow_redirects"] = False' in source
    assert "300 <= int" in source
    assert "blocked an outbound HTTP redirect" in source

    # The private import hook exists only for the lifetime of the subprocess.
    assert not os.path.exists(captured["guard_dir"])


def test_git_and_zabbix_also_explicitly_disable_redirects():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    git_source = (
        root / "app" / "services" / "git.py"
    ).read_text(encoding="utf-8")
    zabbix_source = (
        root / "app" / "services" / "zabbix_inventory.py"
    ).read_text(encoding="utf-8")

    assert 'env["GIT_CONFIG_KEY_0"] = "http.followRedirects"' in git_source
    assert 'env["GIT_CONFIG_VALUE_0"] = "false"' in git_source
    assert "class _NoRedirect(HTTPRedirectHandler)" in zabbix_source
    assert "handlers = [HTTPSHandler(context=context), _NoRedirect()]" in zabbix_source
    assert "build_opener(*handlers)" in zabbix_source
