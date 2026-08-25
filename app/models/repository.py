from datetime import datetime, timezone

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Repository(db.Model):
    """
    An automation content repository available to Journeyman.

    Git repositories track a remote URL and branch. Directory repositories
    snapshot content from a managed local directory.
    Journeyman Projects will later reference a Repository and select
    a playbook from it.
    """

    __tablename__ = "repository"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
    )

    description = db.Column(
        db.String(500),
        nullable=False,
        default="",
    )

    repository_type = db.Column(
        db.String(16),
        nullable=False,
        default="git",
    )

    url = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    directory_path = db.Column(
        db.String(1000),
        nullable=False,
        default="",
    )

    default_branch = db.Column(
        db.String(255),
        nullable=False,
        default="main",
    )

    credential_id = db.Column(
        db.Integer,
        nullable=True,
    )

    status = db.Column(
        db.String(32),
        nullable=False,
        default="never_synced",
    )

    last_sync_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    last_sync_message = db.Column(
        db.Text,
        nullable=True,
    )

    last_commit = db.Column(
        db.String(64),
        nullable=True,
    )

    last_commit_message = db.Column(
        db.String(500),
        nullable=True,
    )

    last_commit_author = db.Column(
        db.String(255),
        nullable=True,
    )

    last_commit_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    def __repr__(self):
        return f"<Repository {self.name!r}>"
