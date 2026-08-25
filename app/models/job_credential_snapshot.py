from app import db
from app.credential_crypto import decrypt_credential_data
from app.credential_types import validate_credential_data


class JobCredentialSnapshot(db.Model):
    """
    Immutable encrypted snapshot of one credential used by a Job.

    The encrypted payload is copied when the Job is queued. This ensures
    that editing or deleting the live Credential cannot alter what the
    queued Job will use.

    Secret values must only be revealed after an explicit owner check.
    The runner may decrypt the snapshot internally for execution.
    """

    __tablename__ = "job_credential_snapshot"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "job.id",
            ondelete="CASCADE",
        ),
    nullable=False,
    index=True,
    )

    # Reference to the original live Credential where it still exists.
    # The snapshot remains valid if the live record is later deleted.
    credential_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "credential.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    credential_name = db.Column(
        db.String(120),
        nullable=False,
    )

    credential_owner = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    credential_type = db.Column(
        db.String(40),
        nullable=False,
        index=True,
    )

    username = db.Column(
        db.String(255),
        nullable=False,
        default="",
    )

    encrypted_data = db.Column(
        db.LargeBinary,
        nullable=False,
    )

    secret_format_version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    credential_key_id = db.Column(
        db.String(64),
        nullable=True,
        index=True,
    )

    job = db.relationship(
        "Job",
        back_populates="credential_snapshots",
    )

    credential = db.relationship(
        "Credential",
    )

    def get_credential_data(self):
        """
        Decrypt and validate the snapshotted credential.

        The caller must perform authorisation before using this method
        to display secret values.
        """

        credential_data = decrypt_credential_data(
            self.encrypted_data,
            self.credential_key_id,
        )

        validate_credential_data(
            self.credential_type,
            credential_data,
        )

        return credential_data

    __table_args__ = (
        db.UniqueConstraint(
            "job_id",
            "credential_id",
            name="uq_job_credential_snapshot",
        ),
    )
    
    def __repr__(self):
        return (
            f"<JobCredentialSnapshot job_id={self.job_id} "
            f"credential_id={self.credential_id} "
            f"name={self.credential_name!r}>"
        )
