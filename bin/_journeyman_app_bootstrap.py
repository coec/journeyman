"""Choose the Journeyman application venv after RPM shebang normalisation.

This module intentionally uses only the Python standard library, because the
system interpreter may not have Flask or any of Journeyman's dependencies.
"""

import os
from pathlib import Path
import sys

APPLICATION_VENV = Path("/opt/journeyman/venv")


def application_venv():
    """A single stable application environment, regardless of Python version."""
    override = os.environ.get("JOURNEYMAN_VENV", "").strip()
    if override and Path(override) != APPLICATION_VENV:
        raise SystemExit(
            "JOURNEYMAN_VENV must be /opt/journeyman/venv; "
            "re-run install-journeyman.yml to migrate this installation."
        )
    return APPLICATION_VENV


def ensure_application_venv():
    """Re-exec a packaged entry point under the application Python."""
    venv = application_venv()
    if not venv.is_absolute():
        raise SystemExit("Journeyman application venv must be an absolute path: {}".format(venv))
    python = venv / "bin" / "python3"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise SystemExit("Journeyman application Python not executable: {}".format(python))

    # Compare sys.prefix, NOT realpath(sys.executable): venv/bin/python3 is
    # commonly a symlink to /usr/bin/python3 and resolves to the same binary.
    if Path(sys.prefix).resolve() == venv.resolve():
        return

    os.execv(str(python), [str(python), *sys.argv])
