from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.security
ROOT = Path(__file__).resolve().parents[2]
PIN_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^\s;]+$")


def _meaningful_lines(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_release_lock_contains_only_exact_version_pins():
    for filename in ("requirements.lock", "requirements-postgresql.lock"):
        for line in _meaningful_lines(ROOT / filename):
            if line.startswith("-r "):
                continue
            assert PIN_RE.fullmatch(line), f"{filename} contains non-exact lock entry: {line}"


def test_direct_requirements_are_represented_in_release_locks():
    base_lock = "\n".join(_meaningful_lines(ROOT / "requirements.lock"))
    postgresql_lock = "\n".join(_meaningful_lines(ROOT / "requirements-postgresql.lock"))

    # Keep this assertion tied to the maintainer-facing direct dependency
    # declaration instead of duplicating versions here.  Security updates are
    # expected to change requirements.txt and the release lock together.
    expected_base = {
        line
        for line in _meaningful_lines(ROOT / "requirements.txt")
        if not line.startswith("-r ")
    }
    for requirement in expected_base:
        assert requirement in base_lock

    assert "-r requirements.lock" in postgresql_lock
    assert "psycopg==3.3.4" in postgresql_lock
    assert "psycopg-binary==3.3.4" in postgresql_lock


def test_production_install_docs_use_release_lock():
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    postgresql = (ROOT / "INSTALL.PostgreSQL.md").read_text(encoding="utf-8")

    assert "pip install -r requirements-postgresql.lock" in install
    assert "pip install -r requirements-postgresql.lock" in postgresql
    assert "pip install -r requirements-postgresql.txt" not in install
    assert "pip install -r requirements-postgresql.txt" not in postgresql
