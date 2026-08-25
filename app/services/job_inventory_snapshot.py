"""
Secure immutable storage for resolved Job inventory snapshots.
"""

import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


DEFAULT_SNAPSHOT_ROOT = Path(
    "/var/lib/journeyman/inventory-snapshots"
)


class JobInventorySnapshotError(Exception):
    """
    Raised when a Job inventory snapshot cannot be stored or read.
    """


def _snapshot_root():
    root = Path(
        os.environ.get(
            "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
            str(DEFAULT_SNAPSHOT_ROOT),
        )
    )

    try:
        root.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        root.chmod(
            0o700
        )

    except OSError as exc:
        raise JobInventorySnapshotError(
            "Unable to prepare the inventory snapshot directory."
        ) from exc

    return root.resolve()


def _validate_inventory_data(inventory_data):
    if not isinstance(inventory_data, dict):
        raise JobInventorySnapshotError(
            "Resolved inventory must be an object."
        )

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars")
    )

    if not isinstance(hostvars, dict):
        raise JobInventorySnapshotError(
            "Resolved inventory has no hostvars mapping."
        )

    return hostvars


def _canonical_bytes(inventory_data):
    _validate_inventory_data(
        inventory_data
    )

    return (
        json.dumps(
            inventory_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def write_job_inventory_snapshot(
    snapshot,
    inventory_data,
):
    """
    Atomically create an immutable snapshot file.

    The snapshot and its Job must already have database IDs.
    """

    if snapshot.id is None or snapshot.job_id is None:
        raise JobInventorySnapshotError(
            "Inventory snapshot must be flushed before storage."
        )

    hostvars = _validate_inventory_data(
        inventory_data
    )

    payload = _canonical_bytes(
        inventory_data
    )

    checksum = hashlib.sha256(
        payload
    ).hexdigest()

    root = _snapshot_root()

    job_directory = (
        root / str(snapshot.job_id)
    )

    try:
        job_directory.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        job_directory.chmod(
            0o700
        )

    except OSError as exc:
        raise JobInventorySnapshotError(
            "Unable to prepare the Job snapshot directory."
        ) from exc

    destination = (
        job_directory
        / "{}.json".format(snapshot.id)
    )

    if destination.exists():
        raise JobInventorySnapshotError(
            "Inventory snapshot file already exists."
        )

    temporary_path = None

    try:
        file_descriptor, temporary_name = (
            tempfile.mkstemp(
                prefix=".{}-".format(snapshot.id),
                suffix=".tmp",
                dir=str(job_directory),
            )
        )

        temporary_path = Path(
            temporary_name
        )

        os.fchmod(
            file_descriptor,
            0o600,
        )

        with os.fdopen(
            file_descriptor,
            mode="wb",
        ) as snapshot_file:
            snapshot_file.write(
                payload
            )

            snapshot_file.flush()
            os.fsync(
                snapshot_file.fileno()
            )

        os.replace(
            str(temporary_path),
            str(destination),
        )

        temporary_path = None

        destination.chmod(
            0o600
        )

    except OSError as exc:
        raise JobInventorySnapshotError(
            "Unable to write the Job inventory snapshot."
        ) from exc

    finally:
        if (
            temporary_path is not None
            and temporary_path.exists()
        ):
            try:
                temporary_path.unlink()
            except OSError:
                pass

    snapshot.host_count = len(
        hostvars
    )

    snapshot.content_path = str(
        destination
    )

    snapshot.content_sha256 = checksum

    return destination


def cleanup_job_inventory_snapshot_files(job_id):
    """Remove the immutable inventory-snapshot directory for one deleted Job."""
    root = _snapshot_root()
    directory = (root / str(int(job_id))).resolve()
    if root not in directory.parents:
        raise JobInventorySnapshotError("Unsafe Job inventory snapshot path.")
    if directory.exists():
        shutil.rmtree(directory)


def delete_job_inventory_snapshot_path(path):
    """
    Remove a snapshot created during a failed database transaction.
    """

    if not path:
        return

    try:
        Path(path).unlink()

    except FileNotFoundError:
        return

    except OSError as exc:
        raise JobInventorySnapshotError(
            "Unable to remove the abandoned snapshot file."
        ) from exc


def _read_verified_snapshot(snapshot):
    if not snapshot.content_path:
        raise JobInventorySnapshotError(
            "Inventory snapshot has no content path."
        )

    root = _snapshot_root()

    path = Path(
        snapshot.content_path
    ).resolve()

    try:
        path.relative_to(
            root
        )
    except ValueError as exc:
        raise JobInventorySnapshotError(
            "Inventory snapshot path escapes the snapshot root."
        ) from exc

    try:
        payload = path.read_bytes()

    except OSError as exc:
        raise JobInventorySnapshotError(
            "Unable to read the inventory snapshot."
        ) from exc

    checksum = hashlib.sha256(
        payload
    ).hexdigest()

    if checksum != snapshot.content_sha256:
        raise JobInventorySnapshotError(
            "Inventory snapshot checksum verification failed."
        )

    return payload


def read_job_inventory_snapshot_data(snapshot):
    """Read, checksum-verify and deserialize one immutable inventory snapshot."""
    payload = _read_verified_snapshot(snapshot)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobInventorySnapshotError(
            "Inventory snapshot contains invalid JSON."
        ) from exc
    _validate_inventory_data(value)
    return value


def _inventory_script_bytes(payload):
    """
    Wrap canonical dynamic-inventory JSON in an executable Ansible
    inventory script.
    """

    encoded_payload = base64.b64encode(
        payload
    ).decode("ascii")

    script = '''#!/opt/journeyman/venv/bin/python3

import base64
import json
import sys


_PAYLOAD = base64.b64decode({encoded_payload})
_INVENTORY = json.loads(
    _PAYLOAD.decode("utf-8")
)



def main():
    if (
        len(sys.argv) == 2
        and sys.argv[1] == "--list"
    ):
        sys.stdout.buffer.write(
            _PAYLOAD
        )

        if not _PAYLOAD.endswith(b"\\n"):
            sys.stdout.buffer.write(
                b"\\n"
            )

        return 0

    if (
        len(sys.argv) == 3
        and sys.argv[1] == "--host"
    ):
        hostvars = (
            _INVENTORY
            .get("_meta", {{}})
            .get("hostvars", {{}})
        )

        json.dump(
            hostvars.get(
                sys.argv[2],
                {{}},
            ),
            sys.stdout,
            sort_keys=True,
        )

        sys.stdout.write("\\n")
        return 0

    json.dump(
        {{}},
        sys.stdout,
    )

    sys.stdout.write("\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.format(
        encoded_payload=repr(
            encoded_payload
        )
    )

    return script.encode(
        "utf-8"
    )


def build_inventory_script_bytes(inventory_data):
    """
    Return an executable dynamic-inventory script for canonical
    Journeyman inventory data.

    The generated script contains the complete inventory payload.
    Callers must protect it as sensitive data and delete it promptly.
    """

    _validate_inventory_data(
        inventory_data
    )

    payload = _canonical_bytes(
        inventory_data
    )

    return _inventory_script_bytes(
        payload
    )

def materialize_job_inventory_snapshot(
    snapshot,
    destination,
):
    """
    Verify a canonical inventory snapshot and materialize it as an
    executable Ansible inventory script.
    """

    payload = _read_verified_snapshot(
        snapshot
    )

    script_payload = (
        _inventory_script_bytes(
            payload
        )
    )

    destination = Path(
        destination
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    if not os.access(
        str(destination.parent),
        os.W_OK | os.X_OK,
    ):
        raise JobInventorySnapshotError(
            "Inventory destination directory is not writable."
        )

    temporary_path = None

    try:
        file_descriptor, temporary_name = (
            tempfile.mkstemp(
                prefix=".inventory-",
                suffix=".tmp",
                dir=str(destination.parent),
            )
        )

        temporary_path = Path(
            temporary_name
        )

        os.fchmod(
            file_descriptor,
            0o700,
        )

        with os.fdopen(
            file_descriptor,
            mode="wb",
        ) as inventory_file:
            inventory_file.write(
                script_payload
            )

            inventory_file.flush()

            os.fsync(
                inventory_file.fileno()
            )

        os.replace(
            str(temporary_path),
            str(destination),
        )

        temporary_path = None

        destination.chmod(
            0o700
        )

    finally:
        if (
            temporary_path is not None
            and temporary_path.exists()
        ):
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return destination
