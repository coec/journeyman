"""Generate self-contained manual bootstrap scripts for remote runners."""

import base64
import hashlib
import shlex
from pathlib import Path

from flask import current_app


class RunnerBootstrapError(RuntimeError):
    pass


def _server_url():
    fqdn = str(current_app.config.get("PUBLIC_FQDN") or "").strip()
    port = int(current_app.config.get("HTTPS_PORT") or 443)
    if not fqdn:
        raise RunnerBootstrapError("Journeyman PUBLIC_FQDN is not configured.")
    return "https://{}{}".format(fqdn, "" if port == 443 else ":{}".format(port))


def _source_file(relative_path):
    root = Path(__file__).resolve().parents[2]
    path = root / relative_path
    if not path.is_file():
        raise RunnerBootstrapError("Bundled runner file is missing: {}".format(path))
    return path.read_bytes()


def _payload_block(name, destination, data):
    encoded = base64.b64encode(data).decode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    return """cat > /tmp/{name}.b64 <<'JOURNEYMAN_PAYLOAD'\n{encoded}\nJOURNEYMAN_PAYLOAD\nbase64 -d /tmp/{name}.b64 > {destination}\nrm -f /tmp/{name}.b64\necho '{digest}  {destination}' | sha256sum -c -\nchmod 0755 {destination}\n""".format(
        name=name,
        encoded=encoded,
        destination=shlex.quote(destination),
        digest=digest,
    )


def generate_bootstrap_script(
    runner,
    *,
    registration_token,
    https_proxy="",
    no_proxy="",
    server_ca_file="",
    management_port=8443,
):
    """Return a root-run bootstrap script containing current runner artefacts."""

    try:
        management_port = int(management_port)
    except (TypeError, ValueError) as exc:
        raise RunnerBootstrapError("Management port must be an integer.") from exc
    if not 1 <= management_port <= 65535:
        raise RunnerBootstrapError("Management port must be between 1 and 65535.")

    token = str(registration_token or "").strip()
    if not token:
        raise RunnerBootstrapError("A one-time registration token is required.")

    server_url = _server_url()
    q = shlex.quote
    proxy_exports = ""
    if str(https_proxy or "").strip():
        value = q(str(https_proxy).strip())
        proxy_exports += "export https_proxy={}\nexport HTTPS_PROXY={}\n".format(value, value)
    if str(no_proxy or "").strip():
        value = q(str(no_proxy).strip())
        proxy_exports += "export no_proxy={}\nexport NO_PROXY={}\n".format(value, value)

    template = r'''#!/bin/bash
set -euo pipefail
umask 027

if [[ $(id -u) -ne 0 ]]; then
  echo 'This bootstrap script must be run as root.' >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo 'Unable to identify the operating system.' >&2
  exit 1
fi
. /etc/os-release
case "${ID:-}" in
  rhel|centos|rocky|almalinux) ;;
  *) echo "Unsupported operating system: ${ID:-unknown}. A RHEL-family host is required." >&2; exit 1 ;;
esac
command -v dnf >/dev/null 2>&1 || { echo 'dnf is required.' >&2; exit 1; }

SERVER_URL=__SERVER_URL__
REGISTRATION_TOKEN=__TOKEN__
RUNNER_NAME=__RUNNER_NAME__
MANAGEMENT_PORT=__MANAGEMENT_PORT__
SERVER_CA_FILE=__SERVER_CA_FILE__
__PROXY_EXPORTS__

echo "Bootstrapping Journeyman runner ${RUNNER_NAME} against ${SERVER_URL}"
dnf -y install python3.14 ansible-core

getent group journeyman >/dev/null || groupadd --system journeyman
id journeyman >/dev/null 2>&1 || useradd --system --gid journeyman --home-dir /var/lib/journeyman --create-home --shell /sbin/nologin journeyman
install -d -o root -g root -m 0755 /opt/journeyman/bin
install -d -o root -g journeyman -m 0750 /etc/journeyman
install -d -o journeyman -g journeyman -m 0700 /etc/journeyman/runner-pki
install -d -o journeyman -g journeyman -m 0750 /var/lib/journeyman
install -d -o journeyman -g journeyman -m 0700 /var/lib/journeyman/remote-jobs
install -d -o journeyman -g journeyman -m 2770 /var/spool/journeyman/signals

PYTHON=/usr/bin/python3.14
[[ -x ${PYTHON} ]] || { echo 'python3.14 was installed but /usr/bin/python3.14 is unavailable.' >&2; exit 1; }
${PYTHON} -m venv /opt/journeyman/venv314
/opt/journeyman/venv314/bin/pip install --upgrade cryptography

# Keep the runtime owned by root while allowing the unprivileged runner service
# account to traverse/read the virtual environment and its installed modules.
chown -R root:journeyman /opt/journeyman/venv314
chmod -R g+rX,o-rwx /opt/journeyman/venv314

command -v runuser >/dev/null 2>&1 || { echo 'runuser is required to validate the runner service account.' >&2; exit 1; }
runuser -u journeyman -- /opt/journeyman/venv314/bin/python - <<'PYTHON_CHECK'
import cryptography
print("Runner Python runtime OK; cryptography {}".format(cryptography.__version__))
PYTHON_CHECK

__RUNNER_PAYLOAD__
__SIGNAL_PAYLOAD__
__SNMP_PAYLOAD__

CA_ARGS=()
if [[ -n "${SERVER_CA_FILE}" ]]; then
  [[ -r "${SERVER_CA_FILE}" ]] || { echo "Configured server CA file is not readable: ${SERVER_CA_FILE}" >&2; exit 1; }
  CA_ARGS=(--ca-file "${SERVER_CA_FILE}")
fi

JOURNEYMAN_REGISTRATION_TOKEN="${REGISTRATION_TOKEN}" \
  /opt/journeyman/venv314/bin/python /opt/journeyman/bin/journeyman-remote-runner register \
    --server "${SERVER_URL}" \
    --token-env JOURNEYMAN_REGISTRATION_TOKEN \
    --config /etc/journeyman/remote-runner.env \
    --work-root /var/lib/journeyman/remote-jobs \
    --signal-spool-root /var/spool/journeyman/signals \
    --pki-root /etc/journeyman/runner-pki \
    --pki-owner journeyman \
    --management-port "${MANAGEMENT_PORT}" \
    "${CA_ARGS[@]}"

chown root:journeyman /etc/journeyman/remote-runner.env
chmod 0640 /etc/journeyman/remote-runner.env

cat > /etc/systemd/system/journeyman-remote-runner.service <<'UNIT'
[Unit]
Description=Journeyman Remote Job Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=journeyman
Group=journeyman
RuntimeDirectory=journeyman
RuntimeDirectoryMode=0755
EnvironmentFile=/etc/journeyman/remote-runner.env
Environment=PATH=/opt/journeyman/venv314/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
Environment="ANSIBLE_SSH_CONTROL_PATH_DIR=/run/journeyman/ansible-cp"
ExecStart=/opt/journeyman/venv314/bin/python /opt/journeyman/bin/journeyman-remote-runner
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/journeyman/remote-jobs /var/spool/journeyman/signals /etc/journeyman/runner-pki

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now journeyman-remote-runner

# Do not report a successful bootstrap merely because systemd accepted the
# start request. Give the runner time to complete its first heartbeat and fail
# loudly if the service enters a restart loop or exits.
runner_active=0
active_checks=0
for _attempt in {1..15}; do
  if systemctl is-active --quiet journeyman-remote-runner; then
    active_checks=$((active_checks + 1))
    if [[ ${active_checks} -ge 3 ]]; then
      runner_active=1
      break
    fi
  else
    active_checks=0
  fi
  sleep 1
done
if [[ ${runner_active} -ne 1 ]]; then
  echo 'Journeyman remote runner failed to become active.' >&2
  systemctl --no-pager --full status journeyman-remote-runner >&2 || true
  journalctl -u journeyman-remote-runner -n 50 --no-pager >&2 || true
  exit 1
fi

systemctl --no-pager --full status journeyman-remote-runner || true

REGISTRATION_TOKEN=''
unset JOURNEYMAN_REGISTRATION_TOKEN
rm -f -- "$0"
echo 'Journeyman remote runner bootstrap completed.'
'''

    replacements = {
        "__SERVER_URL__": q(server_url),
        "__TOKEN__": q(token),
        "__RUNNER_NAME__": q(runner.name),
        "__MANAGEMENT_PORT__": str(management_port),
        "__SERVER_CA_FILE__": q(str(server_ca_file or "").strip()),
        "__PROXY_EXPORTS__": proxy_exports,
        "__RUNNER_PAYLOAD__": _payload_block(
            "journeyman-remote-runner",
            "/opt/journeyman/bin/journeyman-remote-runner",
            _source_file("bin/journeyman-remote-runner"),
        ),
        "__SIGNAL_PAYLOAD__": _payload_block(
            "journeyman-signal-spool",
            "/opt/journeyman/bin/journeyman-signal-spool",
            _source_file("bin/journeyman-signal-spool"),
        ),
        "__SNMP_PAYLOAD__": _payload_block(
            "journeyman-snmp-trap-spool",
            "/opt/journeyman/bin/journeyman-snmp-trap-spool",
            _source_file("bin/journeyman-snmp-trap-spool"),
        ),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def generate_update_script(
    runner,
    *,
    https_proxy="",
    no_proxy="",
):
    """Return a self-contained local update script for an enrolled runner."""

    if not runner.is_registered or not str(runner.runner_uuid or "").strip():
        raise RunnerBootstrapError(
            "Manual update requires an existing enrolled remote runner."
        )

    q = shlex.quote
    proxy_exports = ""
    if str(https_proxy or "").strip():
        value = q(str(https_proxy).strip())
        proxy_exports += "export https_proxy={}\nexport HTTPS_PROXY={}\n".format(value, value)
    if str(no_proxy or "").strip():
        value = q(str(no_proxy).strip())
        proxy_exports += "export no_proxy={}\nexport NO_PROXY={}\n".format(value, value)

    template = r'''#!/bin/bash
set -euo pipefail
umask 027

if [[ $(id -u) -ne 0 ]]; then
  echo 'This update script must be run as root.' >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo 'Unable to identify the operating system.' >&2
  exit 1
fi
. /etc/os-release
case "${ID:-}" in
  rhel|centos|rocky|almalinux) ;;
  *) echo "Unsupported operating system: ${ID:-unknown}. A RHEL-family host is required." >&2; exit 1 ;;
esac
command -v dnf >/dev/null 2>&1 || { echo 'dnf is required.' >&2; exit 1; }
command -v runuser >/dev/null 2>&1 || { echo 'runuser is required.' >&2; exit 1; }

RUNNER_NAME=__RUNNER_NAME__
EXPECTED_UUID=__RUNNER_UUID__
__PROXY_EXPORTS__

CONFIG_FILE=''
for candidate in /etc/journeyman/remote-runner.env /etc/journeyman/remote-runner-*.env; do
  [[ -f "${candidate}" ]] || continue
  configured_uuid=$(sed -n 's/^JOURNEYMAN_RUNNER_UUID=//p' "${candidate}" | head -n 1)
  if [[ "${configured_uuid}" == "${EXPECTED_UUID}" ]]; then
    if [[ -n "${CONFIG_FILE}" ]]; then
      echo "More than one runner configuration contains UUID ${EXPECTED_UUID}." >&2
      exit 1
    fi
    CONFIG_FILE="${candidate}"
  fi
done
if [[ -z "${CONFIG_FILE}" ]]; then
  echo "Unable to locate the enrolled runner configuration for ${RUNNER_NAME} (${EXPECTED_UUID})." >&2
  echo 'Use recovery/re-enrolment rather than update if the local identity was lost.' >&2
  exit 1
fi

RUNNER_PRIVATE_KEY=$(sed -n 's/^JOURNEYMAN_RUNNER_PRIVATE_KEY=//p' "${CONFIG_FILE}" | head -n 1)
RUNNER_CERTIFICATE=$(sed -n 's/^JOURNEYMAN_RUNNER_CERTIFICATE=//p' "${CONFIG_FILE}" | head -n 1)
RUNNER_CA_CERTIFICATE=$(sed -n 's/^JOURNEYMAN_RUNNER_CA_CERTIFICATE=//p' "${CONFIG_FILE}" | head -n 1)
[[ -n "${RUNNER_PRIVATE_KEY}" && -r "${RUNNER_PRIVATE_KEY}" ]] || { echo 'Existing runner private key is missing or unreadable.' >&2; exit 1; }
[[ -n "${RUNNER_CERTIFICATE}" && -r "${RUNNER_CERTIFICATE}" ]] || { echo 'Existing runner certificate is missing or unreadable.' >&2; exit 1; }
[[ -n "${RUNNER_CA_CERTIFICATE}" && -r "${RUNNER_CA_CERTIFICATE}" ]] || { echo 'Existing runner CA certificate is missing or unreadable.' >&2; exit 1; }

if [[ "${CONFIG_FILE}" == '/etc/journeyman/remote-runner.env' ]]; then
  SERVICE_UNIT='journeyman-remote-runner.service'
else
  instance=${CONFIG_FILE#/etc/journeyman/remote-runner-}
  instance=${instance%.env}
  SERVICE_UNIT="journeyman-remote-runner@${instance}.service"
fi

CURRENT_VERSION='unknown'
if [[ -x /opt/journeyman/bin/journeyman-remote-runner ]]; then
  CURRENT_VERSION=$(/opt/journeyman/bin/journeyman-remote-runner --version 2>/dev/null || echo unknown)
fi
echo "Updating Journeyman runner ${RUNNER_NAME} (${EXPECTED_UUID}); current version ${CURRENT_VERSION}."

dnf -y install python3.14 ansible-core
getent group journeyman >/dev/null || { echo 'The journeyman service group is missing. Use bootstrap/recovery instead of update.' >&2; exit 1; }
id journeyman >/dev/null 2>&1 || { echo 'The journeyman service account is missing. Use bootstrap/recovery instead of update.' >&2; exit 1; }
install -d -o root -g root -m 0755 /opt/journeyman/bin

PYTHON=/usr/bin/python3.14
[[ -x ${PYTHON} ]] || { echo 'python3.14 was installed but /usr/bin/python3.14 is unavailable.' >&2; exit 1; }
if [[ ! -x /opt/journeyman/venv314/bin/python ]]; then
  ${PYTHON} -m venv /opt/journeyman/venv314
fi
/opt/journeyman/venv314/bin/pip install --upgrade cryptography
chown -R root:journeyman /opt/journeyman/venv314
chmod -R g+rX,o-rwx /opt/journeyman/venv314
runuser -u journeyman -- /opt/journeyman/venv314/bin/python - <<'PYTHON_CHECK'
import cryptography
print("Runner Python runtime OK; cryptography {}".format(cryptography.__version__))
PYTHON_CHECK

BACKUP_DIR=$(mktemp -d /var/tmp/journeyman-runner-update.XXXXXX)
cleanup() { rm -rf "${BACKUP_DIR}"; }
trap cleanup EXIT
for path in \
  /opt/journeyman/bin/journeyman-remote-runner \
  /opt/journeyman/bin/journeyman-signal-spool \
  /opt/journeyman/bin/journeyman-snmp-trap-spool; do
  [[ -f "${path}" ]] && cp -a "${path}" "${BACKUP_DIR}/"
done

__RUNNER_PAYLOAD__
__SIGNAL_PAYLOAD__
__SNMP_PAYLOAD__

NEW_VERSION=$(/opt/journeyman/bin/journeyman-remote-runner --version)
echo "Bundled runner version: ${NEW_VERSION}"

systemctl daemon-reload
if ! systemctl restart "${SERVICE_UNIT}"; then
  echo "Unable to restart ${SERVICE_UNIT}; restoring previous runner binaries." >&2
  cp -af "${BACKUP_DIR}"/* /opt/journeyman/bin/ 2>/dev/null || true
  systemctl restart "${SERVICE_UNIT}" || true
  exit 1
fi

runner_active=0
active_checks=0
for _attempt in {1..15}; do
  if systemctl is-active --quiet "${SERVICE_UNIT}"; then
    active_checks=$((active_checks + 1))
    if [[ ${active_checks} -ge 3 ]]; then
      runner_active=1
      break
    fi
  else
    active_checks=0
  fi
  sleep 1
done
if [[ ${runner_active} -ne 1 ]]; then
  echo "${SERVICE_UNIT} failed after update; restoring previous runner binaries." >&2
  systemctl --no-pager --full status "${SERVICE_UNIT}" >&2 || true
  journalctl -u "${SERVICE_UNIT}" -n 50 --no-pager >&2 || true
  cp -af "${BACKUP_DIR}"/* /opt/journeyman/bin/ 2>/dev/null || true
  systemctl restart "${SERVICE_UNIT}" || true
  exit 1
fi

INSTALLED_VERSION=$(/opt/journeyman/bin/journeyman-remote-runner --version)
if [[ "${INSTALLED_VERSION}" != "${NEW_VERSION}" ]]; then
  echo "Runner version verification failed: expected ${NEW_VERSION}, got ${INSTALLED_VERSION}." >&2
  exit 1
fi

POST_UPDATE_UUID=$(sed -n 's/^JOURNEYMAN_RUNNER_UUID=//p' "${CONFIG_FILE}" | head -n 1)
if [[ "${POST_UPDATE_UUID}" != "${EXPECTED_UUID}" ]]; then
  echo 'Runner UUID changed during update; refusing to report success.' >&2
  exit 1
fi

systemctl --no-pager --full status "${SERVICE_UNIT}" || true
rm -f -- "$0"
echo "Journeyman remote runner update completed (${CURRENT_VERSION} -> ${INSTALLED_VERSION})."
'''

    replacements = {
        "__RUNNER_NAME__": q(runner.name),
        "__RUNNER_UUID__": q(str(runner.runner_uuid)),
        "__PROXY_EXPORTS__": proxy_exports,
        "__RUNNER_PAYLOAD__": _payload_block(
            "journeyman-remote-runner",
            "/opt/journeyman/bin/journeyman-remote-runner",
            _source_file("bin/journeyman-remote-runner"),
        ),
        "__SIGNAL_PAYLOAD__": _payload_block(
            "journeyman-signal-spool",
            "/opt/journeyman/bin/journeyman-signal-spool",
            _source_file("bin/journeyman-signal-spool"),
        ),
        "__SNMP_PAYLOAD__": _payload_block(
            "journeyman-snmp-trap-spool",
            "/opt/journeyman/bin/journeyman-snmp-trap-spool",
            _source_file("bin/journeyman-snmp-trap-spool"),
        ),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template
