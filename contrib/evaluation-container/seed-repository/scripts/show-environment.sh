#!/usr/bin/env bash
set -euo pipefail

echo "Journeyman script execution environment"
echo "======================================="
echo
echo "Executable: $0"
echo "Working directory: $(pwd)"
echo "Hostname: $(hostname)"
echo "User: $(id -un)"
echo
echo "Journeyman-supplied variables:"

env \
  | grep '^JOURNEYMAN_' \
  | grep -Ev '(PASS|PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL)' \
  | sort \
  || true

echo
echo "Sensitive-looking variables are intentionally omitted."
