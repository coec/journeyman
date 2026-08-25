"""Immutable repository artefacts for remotely dispatched Jobs."""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import current_app

from app.services.git import GitError, safe_repository_dir


class RunnerArtifactError(RuntimeError):
    """A remote-runner artefact could not be prepared safely."""


def _artifact_root():
    configured = current_app.config.get("RUNNER_ARTIFACT_ROOT")
    if configured:
        return Path(configured).resolve()
    repository_root = Path(current_app.config["REPOSITORY_ROOT"]).resolve()
    return (repository_root.parent / "runner-artifacts").resolve()


def _safe_job_directory(job_id):
    root = _artifact_root()
    path = (root / str(int(job_id))).resolve()
    if root not in path.parents:
        raise RunnerArtifactError("Unsafe runner artefact path.")
    return path


def _artifact_filename(snapshot):
    commit = str(snapshot.repository_commit or "").strip().lower()
    if not commit or any(character not in "0123456789abcdef" for character in commit):
        raise RunnerArtifactError("Repository snapshot has an invalid commit identifier.")
    return "repository-{}-{}.tar.gz".format(snapshot.id, commit[:12])


def repository_artifact_path(snapshot):
    directory = _safe_job_directory(snapshot.job_id)
    path = (directory / _artifact_filename(snapshot)).resolve()
    if directory not in path.parents:
        raise RunnerArtifactError("Unsafe repository artefact path.")
    return path


def _run_git(args, cwd):
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    timeout = max(
        1,
        int(
            current_app.config.get(
                "GIT_COMMAND_TIMEOUT_SECONDS",
                300,
            )
        ),
    )
    try:
        process = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerArtifactError(
            "Repository artifact Git command exceeded its {} second timeout."
            .format(timeout)
        ) from exc
    except OSError as exc:
        raise RunnerArtifactError(
            "Unable to execute repository artifact Git command: {}"
            .format(exc)
        ) from exc

    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "Git command failed."
        raise RunnerArtifactError(message)


def _sha256_and_size(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def prepare_repository_artifact(snapshot):
    """Create or reuse a tar.gz archive for one immutable repository snapshot."""
    destination = repository_artifact_path(snapshot)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    if not destination.exists():
        try:
            repository_path = safe_repository_dir(
                current_app.config["REPOSITORY_ROOT"], snapshot.repository_id
            )
        except GitError as exc:
            raise RunnerArtifactError(str(exc)) from exc
        if not (repository_path / ".git").is_dir():
            raise RunnerArtifactError(
                'Repository "{}" is not available locally.'.format(snapshot.repository_name)
            )

        commit = str(snapshot.repository_commit or "").strip()
        _run_git(["cat-file", "-e", "{}^{{commit}}".format(commit)], repository_path)

        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=".repository-artifact-",
            suffix=".tar.gz",
            dir=str(destination.parent),
        )
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            _run_git(
                ["archive", "--format=tar.gz", "--output", str(temporary_path), commit],
                repository_path,
            )
            os.chmod(temporary_path, 0o600)
            temporary_path.replace(destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    checksum, size = _sha256_and_size(destination)
    return {
        "snapshot_id": snapshot.id,
        "repository_name": snapshot.repository_name,
        "commit": snapshot.repository_commit,
        "filename": destination.name,
        "sha256": checksum,
        "size_bytes": size,
    }


def prepare_job_repository_artifacts(job):
    return [prepare_repository_artifact(snapshot) for snapshot in job.repository_snapshots]


def cleanup_job_repository_artifacts(job_id):
    directory = _safe_job_directory(job_id)
    if directory.exists():
        shutil.rmtree(directory)
