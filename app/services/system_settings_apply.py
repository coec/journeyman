import json
import subprocess

from flask import current_app

from app.services.system_settings import (
    configuration_payload,
    configuration_sha256,
)


class SystemSettingsApplyError(RuntimeError):
    pass


def _safe_message(value, fallback):
    message = str(value or "").strip()

    if not message:
        return fallback

    return message[:2000]


def apply_nginx_settings(settings):
    """
    Ask the constrained privileged helper to apply the desired
    Journeyman web configuration.

    Certificate and key contents are never read by the Flask process.
    """

    helper_path = current_app.config[
        "NGINX_APPLY_HELPER"
    ]

    timeout = int(
        current_app.config[
            "NGINX_APPLY_TIMEOUT_SECONDS"
        ]
    )

    payload = configuration_payload(settings)

    expected_digest = configuration_sha256(
        settings
    )

    try:
        completed = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                helper_path,
            ],
            input=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemSettingsApplyError(
            "Nginx configuration application timed out."
        ) from exc
    except OSError as exc:
        raise SystemSettingsApplyError(
            "The Nginx apply helper could not be started."
        ) from exc

    result = None

    if completed.stdout.strip():
        try:
            result = json.loads(
                completed.stdout
            )
        except json.JSONDecodeError:
            result = None

    if completed.returncode != 0:
        if isinstance(result, dict):
            message = result.get("message")
        else:
            message = completed.stderr

        raise SystemSettingsApplyError(
            _safe_message(
                message,
                "The privileged Nginx helper failed.",
            )
        )

    if not isinstance(result, dict):
        raise SystemSettingsApplyError(
            "The Nginx helper returned an invalid response."
        )

    if result.get("ok") is not True:
        raise SystemSettingsApplyError(
            _safe_message(
                result.get("message"),
                "The Nginx helper rejected the settings.",
            )
        )

    returned_digest = result.get(
        "configuration_sha256"
    )

    if returned_digest != expected_digest:
        raise SystemSettingsApplyError(
            "The applied configuration digest did not "
            "match the saved settings."
        )

    return {
        "configuration_sha256": (
            returned_digest
        ),
        "message": _safe_message(
            result.get("message"),
            "Nginx configuration applied successfully.",
        ),
        "public_url": str(
            result.get("public_url") or ""
        ),
    }
