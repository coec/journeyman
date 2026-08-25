from pathlib import Path

from app import db
from app.credential_types import CREDENTIAL_TYPE_SOURCE_CONTROL
from app.models import Credential, Repository
from app.services import git as git_service


def _source_control_credential(name="git-service"):
    credential = Credential(
        name=name,
        description="HTTPS Git credential",
        owner="admin",
        credential_type=CREDENTIAL_TYPE_SOURCE_CONTROL,
        username="svc-git",
    )
    credential.set_credential_data({"password": "super-secret"})
    db.session.add(credential)
    db.session.flush()
    return credential


def test_https_repository_form_requires_source_control_credential(app, client):
    response = client.post(
        "/repositories/new",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "HTTPS repository",
            "repository_type": "git",
            "url": "https://git.example.test/team/repo.git",
            "default_branch": "main",
            "credential_id": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"HTTPS repositories require a Source Control credential." in response.data

    with app.app_context():
        assert Repository.query.filter_by(name="HTTPS repository").first() is None


def test_https_repository_uses_askpass_and_keeps_url_clean(app, monkeypatch, tmp_path):
    captured = []

    def fake_run(args, cwd=None, env=None):
        helper_content = ""
        if env and env.get("GIT_ASKPASS"):
            helper_content = Path(env["GIT_ASKPASS"]).read_text(encoding="utf-8")
        captured.append((list(args), dict(env or {}), helper_content))
        if args[1:3] == ["log", "-1"]:
            return (
                "a" * 40
                + "\x1fmsg\x1fauthor\x1f"
                + "2026-08-15T00:00:00+00:00"
            )
        return ""

    monkeypatch.setattr(git_service, "_run", fake_run)

    with app.app_context():
        credential = _source_control_credential()
        repository = Repository(
            name="HTTPS repository",
            repository_type="git",
            url="https://git.example.test/team/repo.git",
            default_branch="main",
            credential_id=credential.id,
        )
        db.session.add(repository)
        db.session.commit()

        git_service.sync_repository(repository, tmp_path)

    clone_args, clone_env, helper_content = captured[0]
    assert clone_args[0:2] == ["git", "clone"]
    assert "https://git.example.test/team/repo.git" in clone_args
    assert "svc-git" not in " ".join(clone_args)
    assert "super-secret" not in " ".join(clone_args)
    assert clone_env["GIT_TERMINAL_PROMPT"] == "0"
    assert clone_env["JOURNEYMAN_GIT_USERNAME"] == "svc-git"
    assert clone_env["JOURNEYMAN_GIT_PASSWORD"] == "super-secret"
    assert helper_content.startswith("#!/bin/sh")
    assert "super-secret" not in helper_content
    assert "svc-git" not in helper_content


def test_repository_edit_invalid_port_is_validation_error_not_500(app, client):
    with app.app_context():
        credential = _source_control_credential("edit-git")
        repository = Repository(
            name="Edit repository",
            repository_type="git",
            url="https://git.example.test/team/repo.git",
            default_branch="main",
            credential_id=credential.id,
        )
        db.session.add(repository)
        db.session.commit()
        repository_id = repository.id
        credential_id = credential.id

    response = client.post(
        f"/repositories/{repository_id}/edit",
        headers={"X-Test-Username": "admin"},
        data={
            "name": "Edit repository",
            "repository_type": "git",
            "url": "https://git.example.test:omts/team/repo.git",
            "default_branch": "main",
            "credential_id": str(credential_id),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Repository URL contains an invalid port." in response.data
