#!/usr/bin/env bash
set -euo pipefail

source /runner/venv/bin/activate 2>/dev/null || {
  echo "[FAIL] Venv not found at /runner/venv. Run scripts/bootstrap.sh first."
  exit 1
}

cd /workspace

echo "=== Running tests ==="
pytest tests/ -v --tb=short 2>&1 | tee /runner/logs/test-report.log

echo "=== Tests complete ==="
