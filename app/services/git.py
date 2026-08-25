from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager

from flask import current_app

from app.services.outbound_security import (
    OutboundSecurityError,
    validate_repository_url,
)


class GitError(RuntimeError):
    pass


DIRECTORY_REPOSITORY_BASE = Path("/opt/journeyman/repositories")


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    committed_at: datetime


def _run(args, cwd=None, env=None):
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
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "Git command exceeded its {} second timeout.".format(
                timeout
            )
        ) from exc
    except OSError as exc:
        raise GitError(
            "Unable to execute Git command: {}".format(exc)
        ) from exc

    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or "Git command failed")
    return proc.stdout.strip()


def safe_repository_dir(repository_root, repository_id):
    root = Path(repository_root).resolve()
    path = (root / str(repository_id)).resolve()

    if root not in path.parents:
        raise GitError("Unsafe repository path")

    return path


def validate_directory_repository_path(
    directory_path,
    repositories=(),
    exclude_repository_id=None,
):
    """Validate and canonicalise a plain-directory repository source path."""
    raw = str(directory_path or "").strip()
    if not raw:
        raise GitError("Directory path is required.")

    candidate_input = Path(raw)
    if not candidate_input.is_absolute():
        raise GitError(
            "Directory repository path must be absolute and under {}.".format(
                DIRECTORY_REPOSITORY_BASE
            )
        )

    try:
        base = DIRECTORY_REPOSITORY_BASE.resolve(strict=True)
    except OSError as exc:
        raise GitError(
            "Directory repository base does not exist: {}.".format(
                DIRECTORY_REPOSITORY_BASE
            )
        ) from exc

    try:
        candidate = candidate_input.resolve(strict=True)
    except OSError as exc:
        raise GitError(
            "Directory repository does not exist: {}.".format(raw)
        ) from exc

    if not candidate.is_dir():
        raise GitError(
            "Directory repository path is not a directory: {}.".format(candidate)
        )

    if candidate == base or base not in candidate.parents:
        raise GitError(
            "Directory repositories must be directories below {}, not the base "
            "directory itself.".format(base)
        )

    for repository in repositories:
        if getattr(repository, "repository_type", "git") != "directory":
            continue
        if (
            exclude_repository_id is not None
            and getattr(repository, "id", None) == exclude_repository_id
        ):
            continue
        existing_raw = str(getattr(repository, "directory_path", "") or "").strip()
        if not existing_raw:
            continue
        existing = Path(existing_raw).resolve(strict=False)
        if candidate == existing:
            raise GitError(
                "Directory repository path is already used by repository {!r}."
                .format(getattr(repository, "name", existing.name))
            )
        if existing in candidate.parents or candidate in existing.parents:
            raise GitError(
                "Directory repositories cannot be nested. Path {} overlaps "
                "repository {!r} at {}.".format(
                    candidate,
                    getattr(repository, "name", existing.name),
                    existing,
                )
            )

    _validate_directory_repository_tree(candidate)
    return str(candidate)


def _validate_directory_repository_tree(root):
    """Reject source-tree constructs that cannot be snapshotted safely."""
    root = Path(root).resolve()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirnames) + list(filenames):
            path = current_path / name
            if name == ".git":
                raise GitError(
                    "Directory repositories must be plain directories; .git "
                    "metadata is not permitted inside {}.".format(root)
                )
            if path.is_symlink():
                raise GitError(
                    "Symlinks are not permitted in directory repositories: {}."
                    .format(path)
                )
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise GitError(
                    "Special files are not permitted in directory repositories: {}."
                    .format(path)
                )


def _clear_directory_snapshot_worktree(path):
    for entry in path.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _copy_directory_snapshot(source, destination):
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)


def sync_directory_repository(repository, repository_root, repositories=()):
    """Snapshot a managed plain directory into Journeyman's internal Git store."""
    source = Path(
        validate_directory_repository_path(
            repository.directory_path,
            repositories=repositories,
            exclude_repository_id=repository.id,
        )
    )
    path = safe_repository_dir(repository_root, repository.id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and not (path / ".git").is_dir():
        shutil.rmtree(path)
    if not path.exists():
        path.mkdir(parents=True)
        _run(["git", "init", str(path)])

    _run(["git", "config", "user.name", "Journeyman Directory Snapshot"], cwd=path)
    _run(["git", "config", "user.email", "journeyman@localhost"], cwd=path)

    remotes = _run(["git", "remote"], cwd=path).splitlines()
    if "origin" in remotes:
        _run(["git", "remote", "remove", "origin"], cwd=path)

    try:
        _clear_directory_snapshot_worktree(path)
        _copy_directory_snapshot(source, path)
    except OSError as exc:
        raise GitError(
            "Unable to snapshot directory repository {}: {}".format(source, exc)
        ) from exc
    _run(["git", "add", "-A"], cwd=path)

    has_head = True
    try:
        _run(["git", "rev-parse", "--verify", "HEAD"], cwd=path)
    except GitError:
        has_head = False

    changed = bool(_run(["git", "status", "--porcelain"], cwd=path))
    if changed or not has_head:
        _run(
            [
                "git",
                "commit",
                "--allow-empty",
                "-m",
                "Journeyman directory snapshot",
            ],
            cwd=path,
        )

    raw = _run(
        ["git", "log", "-1", "--format=%H%x1f%s%x1f%an%x1f%aI"],
        cwd=path,
    )
    sha, message, author, committed_at = raw.split("\x1f", 3)
    return CommitInfo(
        sha=sha,
        message=message,
        author=author,
        committed_at=datetime.fromisoformat(committed_at).astimezone(timezone.utc),
    )



@contextmanager
def _https_credential_environment(base_env, username, password):
    """Yield a Git environment that supplies HTTPS credentials via askpass.

    The repository URL remains credential-free and the password is not placed on
    the Git command line. The helper itself contains no secret values; it reads
    them from the child-process environment and is removed after the operation.
    """

    with tempfile.TemporaryDirectory(prefix="journeyman-git-askpass-") as temp_dir:
        helper = Path(temp_dir) / "askpass"
        helper.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' \"$JOURNEYMAN_GIT_USERNAME\" ;;\n"
            "  *) printf '%s\\n' \"$JOURNEYMAN_GIT_PASSWORD\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)

        env = dict(base_env)
        env["GIT_ASKPASS"] = str(helper)
        env["JOURNEYMAN_GIT_USERNAME"] = username
        env["JOURNEYMAN_GIT_PASSWORD"] = password
        yield env


def _repository_https_credentials(repository):
    """Return (username, password) for an HTTPS repository or ``None``.

    HTTPS repository credentials are stored in a Source Control credential, not
    embedded in the URL. SSH repositories continue to use normal SSH behaviour.
    """

    repository_url = str(getattr(repository, "url", "") or "").strip()
    if not repository_url.lower().startswith("https://"):
        return None

    credential_id = getattr(repository, "credential_id", None)
    if not credential_id:
        raise GitError(
            "HTTPS repositories require a Source Control credential."
        )

    from app import db
    from app.credential_crypto import CredentialCryptoError
    from app.credential_types import CREDENTIAL_TYPE_SOURCE_CONTROL
    from app.models.credential import Credential

    credential = db.session.get(Credential, credential_id)
    if credential is None or credential.credential_type != CREDENTIAL_TYPE_SOURCE_CONTROL:
        raise GitError(
            "Repository Source Control credential is missing or invalid."
        )

    try:
        credential_data = credential.get_credential_data()
    except (CredentialCryptoError, ValueError) as exc:
        raise GitError(
            "Repository Source Control credential could not be decrypted."
        ) from exc

    username = str(credential.username or "").strip()
    password = str(credential_data.get("password") or "")
    if not username or not password:
        raise GitError(
            "HTTPS repositories require a Source Control credential with a "
            "username and password or access token."
        )

    return username, password


def sync_repository(repository, repository_root, token=None):
    repository_type = str(getattr(repository, "repository_type", "git") or "git")
    if repository_type == "directory":
        from app.models.repository import Repository
        return sync_directory_repository(
            repository,
            repository_root,
            repositories=Repository.query.filter_by(repository_type="directory").all(),
        )
    if repository_type != "git":
        raise GitError("Unsupported repository type: {}".format(repository_type))

    path = safe_repository_dir(repository_root, repository.id)
    try:
        repository_url = validate_repository_url(
            repository.url
        )
    except OutboundSecurityError as exc:
        raise GitError(str(exc)) from exc
    # Preserve the legacy token argument for callers that still supply one, but
    # never persist credentials in the repository URL. Normal Journeyman HTTPS
    # repositories use repository.credential_id instead.
    legacy_token = str(token or "")
    credentials = None if legacy_token else _repository_https_credentials(repository)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Prevent an allowed HTTPS Git URL from redirecting Git to an unapproved
    # destination or plaintext transport.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.followRedirects"
    env["GIT_CONFIG_VALUE_0"] = "false"

    if legacy_token and repository_url.lower().startswith("https://"):
        credentials = ("oauth2", legacy_token)

    @contextmanager
    def authenticated_environment():
        if credentials is None:
            yield env
            return
        with _https_credential_environment(env, *credentials) as authenticated:
            yield authenticated

    with authenticated_environment() as git_env:
        if not (path / ".git").exists():
            if path.exists():
                shutil.rmtree(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _run(
                ["git", "clone", "--branch", repository.default_branch, "--single-branch", repository_url, str(path)],
                env=git_env,
            )
        else:
            remotes = _run(["git", "remote"], cwd=path, env=git_env).splitlines()
            if "origin" in remotes:
                _run(["git", "remote", "set-url", "origin", repository_url], cwd=path, env=git_env)
            else:
                _run(["git", "remote", "add", "origin", repository_url], cwd=path, env=git_env)
            _run(["git", "fetch", "--prune", "origin"], cwd=path, env=git_env)
            _run(
                ["git", "checkout", "-B", repository.default_branch, f"origin/{repository.default_branch}"],
                cwd=path,
                env=git_env,
            )
            _run(["git", "reset", "--hard", f"origin/{repository.default_branch}", ], cwd=path, env=git_env)
            _run(["git", "clean", "-fdx"], cwd=path, env=git_env)

        raw = _run(
            ["git", "log", "-1", "--format=%H%x1f%s%x1f%an%x1f%aI"],
            cwd=path,
            env=git_env,
        )
    sha, message, author, committed_at = raw.split("\x1f", 3)
    return CommitInfo(
        sha=sha,
        message=message,
        author=author,
        committed_at=datetime.fromisoformat(committed_at).astimezone(timezone.utc),
    )


def remove_repository_checkout(repository, repository_root):
    path = safe_repository_dir(repository_root, repository.id)
    if path.exists():
        shutil.rmtree(path)



def install_imported_payload(repository, repository_root, files, push=False):
    """Write JXF payload files into a clean checkout and commit them.

    The destination Repository is an ordinary Git repository, so this works
    independently of whether the remote is GitHub, GitLab, or self-managed.
    Pushing is opt-in because it mutates the configured remote.
    """

    if str(getattr(repository, "repository_type", "git") or "git") != "git":
        raise GitError(
            "JXF payload import requires a Git destination repository."
        )

    path = safe_repository_dir(repository_root, repository.id)
    if not (path / ".git").is_dir():
        raise GitError(
            "Destination repository {!r} has no local checkout; sync it first."
            .format(repository.name)
        )
    if _run(["git", "status", "--porcelain"], cwd=path):
        raise GitError(
            "Destination repository {!r} has uncommitted changes."
            .format(repository.name)
        )

    before = _run(["git", "rev-parse", "HEAD"], cwd=path)
    written = []
    try:
        for relative_name, content in files:
            relative = Path(relative_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise GitError("Unsafe imported payload path: {}".format(relative_name))
            target = (path / relative).resolve()
            if path.resolve() not in target.parents:
                raise GitError("Unsafe imported payload path: {}".format(relative_name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            written.append(relative.as_posix())

        if not written:
            return before
        _run(["git", "add", "--"] + written, cwd=path)
        if not _run(["git", "diff", "--cached", "--name-only"], cwd=path):
            return before
        _run(
            [
                "git",
                "-c", "user.name=Journeyman Import",
                "-c", "user.email=journeyman@localhost",
                "commit", "-m", "Import Journeyman JXF payload",
            ],
            cwd=path,
        )
        commit = _run(["git", "rev-parse", "HEAD"], cwd=path)
        if push:
            _run(
                [
                    "git", "push", "origin",
                    "HEAD:{}".format(repository.default_branch),
                ],
                cwd=path,
            )
        return commit
    except Exception:
        _run(["git", "reset", "--hard", before], cwd=path)
        _run(["git", "clean", "-fd"], cwd=path)
        raise
