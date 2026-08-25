"""Encrypted delivery of immutable Job credentials and inventories to runners."""

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.services.job_inventory_snapshot import read_job_inventory_snapshot_data
from app.services.job_stats import build_step_extra_vars


ENVELOPE_VERSION = 1
KEY_INFO = b"journeyman remote execution data v1"


class RunnerExecutionDataError(Exception):
    """Raised when sensitive remote execution data cannot be prepared."""


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii")


def _derive_key(dispatch_token, salt):
    token = str(dispatch_token or "").encode("utf-8")
    if not token:
        raise RunnerExecutionDataError("Dispatch token is required.")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=KEY_INFO,
    ).derive(token)


def _credential_payload(snapshot):
    try:
        data = snapshot.get_credential_data()
    except Exception as exc:
        raise RunnerExecutionDataError(
            "Unable to decrypt credential snapshot {}.".format(snapshot.id)
        ) from exc
    return {
        "snapshot_id": snapshot.id,
        "name": snapshot.credential_name,
        "owner": snapshot.credential_owner,
        "type": snapshot.credential_type,
        "username": snapshot.username,
        "data": data,
    }


def _inventory_payload(snapshot):
    try:
        data = read_job_inventory_snapshot_data(snapshot)
    except Exception as exc:
        raise RunnerExecutionDataError(
            "Unable to read inventory snapshot {}.".format(snapshot.id)
        ) from exc
    return {
        "snapshot_id": snapshot.id,
        "name": snapshot.inventory_name,
        "type": snapshot.inventory_type,
        "version": snapshot.version,
        "host_count": snapshot.host_count,
        "content_sha256": snapshot.content_sha256,
        "data": data,
    }


def build_execution_data_payload(job):
    """Return the sensitive immutable execution payload for one remote Job."""
    base_extra_vars = (
        job.package_snapshot.get_execution_vars()
        if job.package_snapshot is not None
        else {}
    )
    return {
        "version": 1,
        "job_id": job.id,
        "credentials": [
            _credential_payload(snapshot)
            for snapshot in job.credential_snapshots
        ],
        "package": (
            {
                "definition": job.package_snapshot.get_package_definition(),
                "execution_vars": job.package_snapshot.get_execution_vars(),
            }
            if job.package_snapshot is not None
            else None
        ),
        "inventories": [
            _inventory_payload(snapshot)
            for snapshot in job.inventory_snapshots
        ],
        "steps": [
            {
                "position": step.position,
                "inventory_snapshot_id": step.job_inventory_snapshot_id,
                "credential_snapshot_ids": [
                    snapshot.id for snapshot in step.credential_snapshots
                ],
                "extra_vars": build_step_extra_vars(
                    base_extra_vars,
                    {},
                    step_extra_vars=step.get_extra_vars(),
                ),
            }
            for step in job.steps
        ],
    }


def encrypt_execution_data(job, runner, dispatch_token):
    """Encrypt Job execution data using a key derived from the dispatch token."""
    payload = build_execution_data_payload(job)
    plaintext = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    salt = os.urandom(16)
    nonce = os.urandom(12)
    aad = "journeyman-job:{}:runner:{}".format(
        job.id, runner.runner_uuid
    ).encode("utf-8")
    key = _derive_key(dispatch_token, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)

    return {
        "version": ENVELOPE_VERSION,
        "algorithm": "AES-256-GCM",
        "kdf": "HKDF-SHA256",
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "aad": _b64(aad),
        "ciphertext": _b64(ciphertext),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
    }
