#!/usr/bin/env bash
set -euo pipefail

source /runner/venv/bin/activate 2>/dev/null || {
  echo "[FAIL] Venv not found at /runner/venv. Run scripts/bootstrap.sh first."
  exit 1
}

echo "=== Doctor: environment checks ==="

echo "Python: $(python --version)"
echo "Pip: $(pip --version)"

echo "--- Checking Ollama at ${OLLAMA_BASE_URL:-not set} ---"
if [ -n "${OLLAMA_BASE_URL:-}" ]; then
  if curl -sf --max-time 5 "${OLLAMA_BASE_URL}/api/tags" > /dev/null 2>&1; then
    echo "Ollama: reachable"
  else
    echo "Ollama: NOT reachable (this is OK if you don't need it)"
  fi
else
  echo "Ollama: OLLAMA_BASE_URL not set, skipping"
fi

echo "--- Checking outbound HTTPS ---"
if curl -sf --max-time 5 https://httpbin.org/get > /dev/null 2>&1; then
  echo "HTTPS: OK"
else
  echo "HTTPS: FAILED"
  exit 1
fi

echo "=== Doctor passed ==="
