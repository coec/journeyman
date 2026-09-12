import json

import pytest

from app import db
from app.models import (
    PROJECT_APPROVAL_DEVELOPMENT,
    Project,
    ProjectRevision,
    ProjectStep,
    Repository,
)
from app.services.project_revisions import (
    build_project_revision_snapshot,
    capture_project_revision,
    project_revision_digest,
)


def _project_with_git_step():
    repository = Repository(
        name="Revision Test Repository",
        description="",
        repository_type="git",
        url="https://git.invalid/journeyman/revision-test.git",
        default_branch="main",
        last_commit="a" * 40,
    )
    project = Project(
        name="Revision Test Project",
        description="Revision test",
        enabled=True,
        owner="author",
        security_scope="private",
        repository=repository,
        execution_type="ansible",
    )
    step = ProjectStep(
        position=1,
        name="Run test playbook",
        execution_type="ansible",
        playbook="test.yml",
    )
    project.steps.append(step)
    db.session.add(project)
    db.session.flush()
    return project, repository, step


def test_project_defaults_to_development_approval_state(app):
    with app.app_context():
        project = Project(
            name="Approval State Project",
            description="",
            owner="author",
            security_scope="private",
        )
        db.session.add(project)
        db.session.flush()

        assert project.approval_state == PROJECT_APPROVAL_DEVELOPMENT


def test_invalid_project_approval_state_is_rejected(app):
    with app.app_context():
        with pytest.raises(ValueError):
            Project(
                name="Invalid Approval State Project",
                description="",
                owner="author",
                security_scope="private",
                approval_state="magic",
            )


def test_project_revision_snapshot_records_git_sha_and_steps(app):
    with app.app_context():
        project, repository, _step = _project_with_git_step()

        snapshot = build_project_revision_snapshot(project)

        assert snapshot["schema_version"] == 1
        assert snapshot["project"]["repository"]["last_commit"] == repository.last_commit
        assert snapshot["steps"][0]["effective_repository"]["last_commit"] == repository.last_commit
        assert snapshot["steps"][0]["playbook"] == "test.yml"
        assert len(project_revision_digest(snapshot)) == 64


def test_captured_project_revisions_are_historical_snapshots(app):
    with app.app_context():
        project, repository, step = _project_with_git_step()

        first = capture_project_revision(project, created_by="alice")
        first_id = first.id
        first_digest = first.digest
        first_snapshot = json.loads(first.snapshot_json)

        step.playbook = "changed.yml"
        repository.last_commit = "b" * 40
        db.session.flush()

        second = capture_project_revision(project, created_by="alice")
        db.session.commit()

        first = db.session.get(ProjectRevision, first_id)
        assert first.sequence == 1
        assert second.sequence == 2
        assert first.digest == first_digest
        assert second.digest != first_digest
        assert first.snapshot()["steps"][0]["playbook"] == "test.yml"
        assert first.snapshot()["project"]["repository"]["last_commit"] == "a" * 40
        assert second.snapshot()["steps"][0]["playbook"] == "changed.yml"
        assert first_snapshot == first.snapshot()
