import io
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from app.services.git import GitError, safe_repository_dir
from app.services.runner_artifacts import RunnerArtifactError, _artifact_filename

pytestmark = pytest.mark.security


def _load_remote_runner():
    path = Path(__file__).resolve().parents[2] / "bin" / "journeyman-remote-runner"
    loader = SourceFileLoader("journeyman_remote_runner_file_security", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_tar(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for member, content in members:
            payload = None if content is None else content.encode("utf-8")
            if payload is not None:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                archive.addfile(member)


def test_repository_path_cannot_escape_configured_root(tmp_path):
    root = tmp_path / "repositories"
    root.mkdir()

    with pytest.raises(GitError, match="Unsafe repository path"):
        safe_repository_dir(root, "../../outside")


def test_runner_artifact_filename_uses_internal_ids_not_repository_name():
    snapshot = SimpleNamespace(
        id=17,
        repository_commit="a" * 40,
        repository_name='../../evil\r\nContent-Disposition: inline',
    )

    assert _artifact_filename(snapshot) == "repository-17-aaaaaaaaaaaa.tar.gz"


def test_runner_artifact_filename_rejects_non_hex_commit():
    snapshot = SimpleNamespace(
        id=17,
        repository_commit="../../etc/passwd",
        repository_name="repo",
    )

    with pytest.raises(RunnerArtifactError, match="invalid commit identifier"):
        _artifact_filename(snapshot)


def test_remote_runner_safe_extract_rejects_parent_traversal(tmp_path):
    runner = _load_remote_runner()
    archive_path = tmp_path / "repository.tar.gz"
    member = tarfile.TarInfo("../../escaped.txt")
    _write_tar(archive_path, [(member, "owned")])

    destination = tmp_path / "workspace"
    destination.mkdir()

    with pytest.raises(RuntimeError, match="unsafe path"):
        runner.safe_extract(archive_path, destination)

    assert not (tmp_path / "escaped.txt").exists()


def test_remote_runner_safe_extract_rejects_symlink_and_hardlink(tmp_path):
    runner = _load_remote_runner()

    for name, member_type in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)):
        archive_path = tmp_path / f"{name}.tar.gz"
        member = tarfile.TarInfo(name)
        member.type = member_type
        member.linkname = "/etc/passwd"
        _write_tar(archive_path, [(member, None)])
        destination = tmp_path / f"{name}-workspace"
        destination.mkdir()

        with pytest.raises(RuntimeError, match="contains a link"):
            runner.safe_extract(archive_path, destination)


def test_remote_runner_safe_extract_rejects_special_files(tmp_path):
    runner = _load_remote_runner()
    archive_path = tmp_path / "fifo.tar.gz"
    member = tarfile.TarInfo("named-pipe")
    member.type = tarfile.FIFOTYPE
    _write_tar(archive_path, [(member, None)])
    destination = tmp_path / "workspace"
    destination.mkdir()

    with pytest.raises(RuntimeError, match="unsupported special file"):
        runner.safe_extract(archive_path, destination)


def test_remote_runner_safe_extract_accepts_regular_files_and_directories(tmp_path):
    runner = _load_remote_runner()
    archive_path = tmp_path / "good.tar.gz"
    directory = tarfile.TarInfo("roles")
    directory.type = tarfile.DIRTYPE
    file_member = tarfile.TarInfo("roles/main.yml")
    _write_tar(archive_path, [(directory, None), (file_member, "---\n")])
    destination = tmp_path / "workspace"
    destination.mkdir()

    runner.safe_extract(archive_path, destination)

    assert (destination / "roles" / "main.yml").read_text() == "---\n"


def test_directory_repository_must_be_child_of_fixed_base(monkeypatch, tmp_path):
    from app.services import git as git_service

    base = tmp_path / "managed-repositories"
    base.mkdir()
    child = base / "network"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(git_service, "DIRECTORY_REPOSITORY_BASE", base)

    assert git_service.validate_directory_repository_path(child) == str(child.resolve())

    with pytest.raises(GitError, match="below"):
        git_service.validate_directory_repository_path(base)

    with pytest.raises(GitError, match="below"):
        git_service.validate_directory_repository_path(outside)


def test_directory_repositories_cannot_be_nested(monkeypatch, tmp_path):
    from app.services import git as git_service

    base = tmp_path / "managed-repositories"
    existing_path = base / "oracle"
    child = existing_path / "tools"
    sibling = base / "network"
    child.mkdir(parents=True)
    sibling.mkdir()
    monkeypatch.setattr(git_service, "DIRECTORY_REPOSITORY_BASE", base)

    existing = SimpleNamespace(
        id=10,
        name="Oracle",
        repository_type="directory",
        directory_path=str(existing_path),
    )

    with pytest.raises(GitError, match="cannot be nested"):
        git_service.validate_directory_repository_path(child, [existing])

    with pytest.raises(GitError, match="cannot be nested"):
        git_service.validate_directory_repository_path(existing_path, [
            SimpleNamespace(
                id=11,
                name="Oracle tools",
                repository_type="directory",
                directory_path=str(child),
            )
        ])

    assert git_service.validate_directory_repository_path(sibling, [existing]) == str(sibling.resolve())


def test_directory_repository_rejects_symlinks_and_git_metadata(monkeypatch, tmp_path):
    from app.services import git as git_service

    base = tmp_path / "managed-repositories"
    repository = base / "network"
    repository.mkdir(parents=True)
    monkeypatch.setattr(git_service, "DIRECTORY_REPOSITORY_BASE", base)

    target = repository / "target.yml"
    target.write_text("---\n")
    (repository / "link.yml").symlink_to("target.yml")
    with pytest.raises(GitError, match="Symlinks are not permitted"):
        git_service.validate_directory_repository_path(repository)

    (repository / "link.yml").unlink()
    (repository / ".git").mkdir()
    with pytest.raises(GitError, match="plain directories"):
        git_service.validate_directory_repository_path(repository)
