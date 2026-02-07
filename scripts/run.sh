#!/usr/bin/env bash
set -euo pipefail

source /runner/venv/bin/activate 2>/dev/null || {
  echo "[FAIL] Venv not found at /runner/venv. Run scripts/bootstrap.sh first."
  exit 1
}

cd /workspace
exec python -m poc.cli "$@"
