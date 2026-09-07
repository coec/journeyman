#!/usr/bin/env bash
set -euo pipefail

echo "Hello from a Journeyman script."
echo "Host: $(hostname)"
echo "User: $(id -un)"
echo "Working directory: $(pwd)"
echo "Time: $(date -Is)"
