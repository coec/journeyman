"""Immutable repository artefacts for remotely dispatched Jobs."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import tarfile
from pathlib import Path

from flask import current_app

from app.services.git import GitError, safe_repository_dir
from app.services.ansible_role_dependencies import (
    RoleDependencyError, install_repository_roles,
)


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



def _add_resolved_roles(source_archive, destination_archive, resolved_roles):
    """Bundle roles into the commit archive without changing the Git checkout."""
    with tarfile.open(source_archive, "r:gz") as source, tarfile.open(
        destination_archive, "w:gz"
    ) as output:
        existing = set()
        for member in source:
            existing.add(member.name.rstrip("/"))
            stream = source.extractfile(member) if member.isfile() else None
            output.addfile(member, stream)
            if stream is not None:
                stream.close()

        for role in sorted(resolved_roles.iterdir()):
            if not role.is_dir() or role.is_symlink() or role.name == ".git":
                raise RunnerArtifactError("Resolved role contains an unsupported entry.")
            role_prefix = "roles/{}".format(role.name)
            if any(
                name == role_prefix or name.startswith(role_prefix + "/")
                for name in existing
            ):
                raise RunnerArtifactError(
                    "Resolved role {!r} conflicts with files tracked in Git."
                    .format(role.name)
                )

            def validate_role_member(info):
                if info.name.split("/")[-1] == ".git":
                    return None
                if not (info.isfile() or info.isdir()):
                    raise RunnerArtifactError(
                        "Resolved role contains an unsafe link or special file."
                    )
                return info

            output.add(str(role), arcname=role_prefix, filter=validate_role_member)


def _include_role_dependencies(archive_path, scratch_directory):
    """Read requirements from the pinned commit, install, then repackage."""
    with tarfile.open(archive_path, "r:gz") as archive:
        try:
            member = archive.getmember("roles/requirements.yml")
        except KeyError:
            return
        if not member.isfile():
            raise RunnerArtifactError("roles/requirements.yml must be a regular file.")
        if member.size > 1024 * 1024:
            raise RunnerArtifactError("roles/requirements.yml is too large.")
        with archive.extractfile(member) as stream:
            requirements_bytes = stream.read()

    requirements = scratch_directory / "requirements.yml"
    requirements.write_bytes(requirements_bytes)
    roles_directory = scratch_directory / "resolved-roles"
    try:
        installed = install_repository_roles(requirements, roles_directory)
    except RoleDependencyError as exc:
        raise RunnerArtifactError(str(exc)) from exc
    if not installed:
        return
    if not roles_directory.exists() or not any(roles_directory.iterdir()):
        raise RunnerArtifactError("Role installation returned no roles.")

    merged_path = scratch_directory / "repository-with-roles.tar.gz"
    _add_resolved_roles(archive_path, merged_path, roles_directory)
    merged_path.replace(archive_path)


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
            with tempfile.TemporaryDirectory(
                prefix=".repository-roles-", dir=str(destination.parent)
            ) as scratch:
                _include_role_dependencies(temporary_path, Path(scratch))
            os.chmod(temporary_path, 0o600)
            # Another execution slice can prepare this same Job concurrently.
            # Publish only if absent: never replace an artefact already handed
            # to a runner, even when mutable role branches have advanced.
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                pass
        except (OSError, tarfile.TarError) as exc:
            raise RunnerArtifactError(
                "Unable to assemble the repository and its role dependencies."
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

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
