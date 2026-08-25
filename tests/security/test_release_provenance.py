import json
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.security
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_release_provenance.py"


def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_provenance_contains_locked_transitive_dependency_inventory(tmp_path):
    result = _run(
        "generate",
        "--root",
        ROOT,
        "--output",
        tmp_path,
        "--lock",
        "requirements-postgresql.lock",
    )
    assert result.returncode == 0, result.stderr

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (tmp_path / "journeyman-{}.manifest.json".format(version)).read_text(
            encoding="utf-8"
        )
    )
    sbom = json.loads(
        (tmp_path / "journeyman-{}.sbom.spdx.json".format(version)).read_text(
            encoding="utf-8"
        )
    )

    packages = {
        (row["name"].casefold(), row["version"])
        for row in manifest["dependency_lock"]["dependencies"]
    }
    assert ("flask", "3.1.3") in packages
    assert ("werkzeug", "3.1.8") in packages
    assert ("psycopg", "3.3.4") in packages

    assert manifest["dependency_lock"]["path"] == "requirements-postgresql.lock"
    assert len(manifest["dependency_lock"]["sha256"]) == 64
    assert manifest["files"]
    assert all(len(row["sha256"]) == 64 for row in manifest["files"])

    assert sbom["spdxVersion"] == "SPDX-2.3"
    sbom_packages = {
        (row["name"].casefold(), row.get("versionInfo"))
        for row in sbom["packages"]
    }
    assert ("journeyman", version) in sbom_packages
    assert ("flask", "3.1.3") in sbom_packages
    assert ("werkzeug", "3.1.8") in sbom_packages
    assert ("psycopg", "3.3.4") in sbom_packages


def test_release_manifest_detects_post_release_source_modification(tmp_path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    (release_root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (release_root / "requirements.lock").write_text(
        "Flask==3.1.1\nWerkzeug==3.1.8\n",
        encoding="utf-8",
    )
    (release_root / "requirements-postgresql.lock").write_text(
        "-r requirements.lock\npsycopg==3.3.4\n",
        encoding="utf-8",
    )
    application_file = release_root / "application.py"
    application_file.write_text("VALUE = 1\n", encoding="utf-8")

    output = tmp_path / "dist"
    generated = _run(
        "generate",
        "--root",
        release_root,
        "--output",
        output,
        "--lock",
        "requirements-postgresql.lock",
    )
    assert generated.returncode == 0, generated.stderr

    manifest = output / "journeyman-1.2.3.manifest.json"
    verified = _run("verify", manifest, "--root", release_root)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "VERIFIED" in verified.stdout

    application_file.write_text("VALUE = 2\n", encoding="utf-8")

    modified = _run("verify", manifest, "--root", release_root)
    assert modified.returncode == 1
    assert "modified: application.py" in modified.stdout


def test_release_manifest_excludes_runtime_secrets_and_source_control(tmp_path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    (release_root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (release_root / "requirements.lock").write_text(
        "Flask==3.1.1\n",
        encoding="utf-8",
    )
    (release_root / "requirements-postgresql.lock").write_text(
        "-r requirements.lock\npsycopg==3.3.4\n",
        encoding="utf-8",
    )
    (release_root / "safe.py").write_text("safe = True\n", encoding="utf-8")

    (release_root / ".git").mkdir()
    (release_root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    (release_root / "instance").mkdir()
    (release_root / "instance" / "runtime.db").write_text("state\n", encoding="utf-8")
    (release_root / "session-signing.key").write_text("secret\n", encoding="utf-8")
    (release_root / "application.log").write_text("runtime\n", encoding="utf-8")

    output = tmp_path / "dist"
    result = _run(
        "generate",
        "--root",
        release_root,
        "--output",
        output,
        "--lock",
        "requirements-postgresql.lock",
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads(
        (output / "journeyman-1.0.0.manifest.json").read_text(encoding="utf-8")
    )
    paths = {row["path"] for row in manifest["files"]}

    assert "safe.py" in paths
    assert ".git/config" not in paths
    assert "instance/runtime.db" not in paths
    assert "session-signing.key" not in paths
    assert "application.log" not in paths
