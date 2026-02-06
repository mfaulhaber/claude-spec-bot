#!/usr/bin/env bash
set -euo pipefail

# Activate persistent venv
source /runner/venv/bin/activate 2>/dev/null || {
  echo "[FAIL] Venv not found at /runner/venv. Run scripts/bootstrap.sh first."
  exit 1
}

cd /workspace

echo "=== Lint: ruff check ==="
ruff check src/ tests/ orchestrator_host/

echo "=== Lint: ruff format check ==="
ruff format --check src/ tests/ orchestrator_host/

echo "=== Lint passed ==="
