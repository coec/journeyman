"""AAP-compatible role dependencies for local and remote Job snapshots."""

import hashlib
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import ansible_role_dependencies as roles
from app.services import runner_artifacts as artifacts


REQUIREMENTS = """\
- src: git@gitlab.scada.horizonpower.com.au:omts/ansible/roles/common_files.git
  scm: git
  name: common_files
  version: master
"""


def _git(directory, *args):
    return subprocess.run(
        ["git", *args], cwd=directory, check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.strip()


def _snapshot(app, tmp_path, requirements=None):
    repo = Path(app.config["REPOSITORY_ROOT"]) / "198"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.org")
    (repo / "baseline.yml").write_text("---\n- hosts: all\n  tasks: []\n")
    if requirements is not None:
        (repo / "roles").mkdir()
        (repo / "roles" / "requirements.yml").write_text(requirements)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "snapshot")
    commit = _git(repo, "rev-parse", "HEAD")
    return SimpleNamespace(
        id=78, job_id=99, repository_id=198, repository_name="Sys Admin",
        repository_commit=commit,
    ), repo


def test_snapshot_without_requirements_remains_ordinary_git_archive(app, tmp_path, monkeypatch):
    snapshot, _ = _snapshot(app, tmp_path)
    monkeypatch.setattr(artifacts, "install_repository_roles", lambda *args: pytest.fail("unexpected Galaxy invocation"))
    with app.app_context():
        details = artifacts.prepare_repository_artifact(snapshot)
        path = artifacts.repository_artifact_path(snapshot)
    assert details["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with tarfile.open(path, "r:gz") as archive:
        assert "baseline.yml" in archive.getnames()
        assert not any(name.startswith("roles/common_files") for name in archive.getnames())


def test_git_snapshot_bundles_resolved_roles_and_reuses_archive(app, tmp_path, monkeypatch):
    snapshot, repo = _snapshot(app, tmp_path, REQUIREMENTS)
    calls = []

    def install(requirements, destination):
        calls.append(Path(requirements).read_text())
        role = Path(destination) / "common_files" / "tasks"
        role.mkdir(parents=True)
        (role / "main.yml").write_text("---\n- debug: msg=installed\n")
        return True

    monkeypatch.setattr(artifacts, "install_repository_roles", install)
    # A newer sync must not change the requirements of the pinned commit.
    (repo / "roles" / "requirements.yml").write_text("- src: changed.role\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "newer commit")

    with app.app_context():
        first = artifacts.prepare_repository_artifact(snapshot)
        second = artifacts.prepare_repository_artifact(snapshot)
        archive_path = artifacts.repository_artifact_path(snapshot)
    assert first == second
    assert calls == [REQUIREMENTS]
    with tarfile.open(archive_path, "r:gz") as archive:
        assert archive.extractfile("roles/requirements.yml").read().decode() == REQUIREMENTS
        assert b"installed" in archive.extractfile("roles/common_files/tasks/main.yml").read()
        assert not any(name.startswith(".git") for name in archive.getnames())
    assert first["size_bytes"] == archive_path.stat().st_size


def test_failed_role_install_does_not_publish_partial_artifact(app, tmp_path, monkeypatch):
    snapshot, _ = _snapshot(app, tmp_path, REQUIREMENTS)

    def fail(*args):
        raise roles.RoleDependencyError("Cannot access private GitLab role")

    monkeypatch.setattr(artifacts, "install_repository_roles", fail)
    with app.app_context():
        with pytest.raises(artifacts.RunnerArtifactError, match="GitLab"):
            artifacts.prepare_repository_artifact(snapshot)
        assert not artifacts.repository_artifact_path(snapshot).exists()


@pytest.mark.parametrize("src", (
    "../../private",
    "file:///etc/passwd",
    "http://localhost/x.git",
    "http://gitlab.scada.horizonpower.com.au/x.git",
    "https://localhost/x.git",
    "ssh://git@localhost/x.git",
    "git@localhost:x.git",
    "https://127.0.0.1/x.git",
))
def test_invalid_role_source_is_rejected_before_network_access(app, src):
    with app.app_context():
        with pytest.raises(roles.RoleDependencyError):
            roles.read_role_requirements(
                "- src: {}\n  name: common_files\n  scm: git\n".format(src)
            )


def test_invalid_role_install_name_is_rejected(app):
    with app.app_context():
        with pytest.raises(roles.RoleDependencyError, match="safe directory"):
            roles.read_role_requirements(
                "- src: git@gitlab.scada.horizonpower.com.au:foo.git\n"
                "  scm: git\n  name: ../escape\n"
            )


def test_valid_git_role_source_is_preserved(app):
    with app.app_context():
        assert roles.read_role_requirements(REQUIREMENTS)[0]["name"] == "common_files"


def test_galaxy_role_install_is_private_and_uses_noninteractive_git(app, tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.yml"
    requirements.write_text(REQUIREMENTS)
    target = tmp_path / "only-this-job" / "roles"
    monkeypatch.setattr(roles.shutil, "which", lambda exe: "/usr/bin/ansible-galaxy")
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    monkeypatch.setattr(roles.subprocess, "run", run)
    with app.app_context():
        assert roles.install_repository_roles(requirements, target)
    command, arguments = seen[0]
    assert command[-2:] == ["-p", str(target)]
    assert arguments["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert arguments["env"]["GIT_CONFIG_VALUE_0"] == "false"


def test_resolved_role_symlinks_are_not_packaged(app, tmp_path, monkeypatch):
    snapshot, _ = _snapshot(app, tmp_path, REQUIREMENTS)

    def install(requirements, destination):
        target = Path(destination) / "common_files"
        target.mkdir(parents=True)
        (target / "secrets").symlink_to("/etc/shadow")
        return True

    monkeypatch.setattr(artifacts, "install_repository_roles", install)
    with app.app_context():
        with pytest.raises(artifacts.RunnerArtifactError, match="unsafe link"):
            artifacts.prepare_repository_artifact(snapshot)
        assert not artifacts.repository_artifact_path(snapshot).exists()
