from types import SimpleNamespace

from app.services import git as git_service


def test_directory_repository_sync_creates_immutable_internal_git_snapshots(
    app, monkeypatch, tmp_path
):
    base = tmp_path / "managed-repositories"
    source = base / "network"
    source.mkdir(parents=True)
    (source / "recover.yml").write_text("---\n- hosts: all\n")
    repository_root = tmp_path / "internal-repositories"
    repository_root.mkdir()

    monkeypatch.setattr(git_service, "DIRECTORY_REPOSITORY_BASE", base)
    repository = SimpleNamespace(
        id=991,
        name="Network directory",
        repository_type="directory",
        directory_path=str(source),
    )

    with app.app_context():
        first = git_service.sync_directory_repository(
            repository, repository_root, repositories=[]
        )
        second = git_service.sync_directory_repository(
            repository, repository_root, repositories=[]
        )

        assert first.sha == second.sha
        checkout = git_service.safe_repository_dir(repository_root, repository.id)
        assert (checkout / ".git").is_dir()
        assert (checkout / "recover.yml").read_text() == "---\n- hosts: all\n"

        (source / "recover.yml").write_text("---\n- hosts: all\n  gather_facts: false\n")
        third = git_service.sync_directory_repository(
            repository, repository_root, repositories=[]
        )

    assert third.sha != first.sha
    assert source.is_dir()
    assert (source / "recover.yml").is_file()
