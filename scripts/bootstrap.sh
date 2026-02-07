#!/usr/bin/env bash
set -euo pipefail

echo "=== Bootstrap: creating persistent venv ==="
python -m venv /runner/venv
source /runner/venv/bin/activate

cd /workspace

echo "=== Bootstrap: installing project (dev extras) ==="
pip install --upgrade pip -q
pip install -e ".[dev]" -q

echo "=== Bootstrap complete ==="
