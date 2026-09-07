#!/usr/bin/env bash
set -euo pipefail

cd /opt/journeyman

DATA_ROOT=/var/lib/journeyman
SECRET_ROOT="${DATA_ROOT}/secrets"
DEMO_REPOSITORY="${DATA_ROOT}/repositories/demo-repository"
SEED_REPOSITORY=/opt/journeyman/contrib/evaluation-container/seed-repository

mkdir -p \
    "${SECRET_ROOT}" \
    "${DATA_ROOT}/repositories" \
    "${DATA_ROOT}/jobs" \
    "${DATA_ROOT}/runner-artifacts" \
    "${DEMO_REPOSITORY}" \
    /var/log/journeyman

mkdir -p /etc/journeyman

if [[ ! -s "${SECRET_ROOT}/credential.key" ]]; then
    /opt/journeyman/venv/bin/python - <<'PY'
from pathlib import Path
from cryptography.fernet import Fernet

path = Path("/var/lib/journeyman/secrets/credential.key")
path.write_bytes(Fernet.generate_key() + b"\n")
path.chmod(0o600)
PY
fi

ln -sfn \
    "${SECRET_ROOT}/credential.key" \
    /etc/journeyman/credential.key

if [[ ! -s "${SECRET_ROOT}/session-signing.key" ]]; then
    python - <<'PY'
from pathlib import Path
import secrets

path = Path("/var/lib/journeyman/secrets/session-signing.key")
path.write_text(secrets.token_urlsafe(64) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
fi

# Copy the example content into persistent storage only once. Evaluators can
# subsequently modify the files without an image restart overwriting them.
if [[ ! -e "${DEMO_REPOSITORY}/.journeyman-demo-initialized" ]]; then
    cp -a "${SEED_REPOSITORY}/." "${DEMO_REPOSITORY}/"
    touch "${DEMO_REPOSITORY}/.journeyman-demo-initialized"
fi

echo "Applying database migrations..."
/opt/journeyman/venv/bin/flask --app run.py db upgrade

echo "Ensuring the evaluation disk repository exists..."
PYTHONPATH=/opt/journeyman \
    /opt/journeyman/venv/bin/python \
    /opt/journeyman/contrib/evaluation-container/seed_demo.py

if [[ ! -s "${SECRET_ROOT}/fallback-admin-password.hash" ]]; then
    echo
    echo "Creating the evaluation fallback administrator."
    echo "The generated password is shown once below."
    echo
    /opt/journeyman/venv/bin/flask \
        --app run.py \
        fallback-admin generate \
        --no-expiry
    echo
fi

pids=()

stop_children() {
    local pid
    for pid in "${pids[@]:-}"; do
        kill -TERM "${pid}" 2>/dev/null || true
    done
    wait || true
}

trap stop_children TERM INT EXIT

echo "Starting Journeyman scheduler..."
/opt/journeyman/venv/bin/flask \
    --app run.py \
    run-scheduler \
    --poll-seconds 30 &
pids+=("$!")

echo "Starting Journeyman local runner..."
/opt/journeyman/bin/journeyman-runner &
pids+=("$!")

echo "Starting Journeyman environment builder..."
/opt/journeyman/bin/journeyman-environment-builder &
pids+=("$!")

echo "Starting Journeyman web application on port 5000..."
/opt/journeyman/venv/bin/gunicorn \
    --worker-class gthread \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --bind 0.0.0.0:5000 \
    run:app &
pids+=("$!")

echo
echo "Journeyman evaluation container is running."
echo "Open http://localhost:8080/ when using the supplied compose.yaml."
echo
echo "Sample disk repository:"
echo "  ${DEMO_REPOSITORY}"
echo
echo "Sample static inventory:"
echo "  ${DEMO_REPOSITORY}/inventories/localhost.yml"
echo
echo "If the fallback activation has been ended by signing out, create another:"
echo "  docker compose exec journeyman flask --app run.py fallback-admin generate --no-expiry"
echo

# Exit if any essential child exits. Docker/Compose can then restart the
# evaluation container rather than leaving a partially functional instance.
set +e
wait -n "${pids[@]}"
rc=$?
set -e

echo "A Journeyman evaluation process exited with status ${rc}; stopping."
exit "${rc}"
