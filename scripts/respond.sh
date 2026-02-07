#!/usr/bin/env bash
set -euo pipefail

JOB_ID="${JOB_ID:-unknown}"

RESPONSE_DIR="/runner/jobs/${JOB_ID}/artifacts"
mkdir -p "$RESPONSE_DIR"

cat > "${RESPONSE_DIR}/response.txt" <<EOF
Job ${JOB_ID} has been processed successfully by the runner.
EOF

echo "=== Runner Response ==="
cat "${RESPONSE_DIR}/response.txt"
echo "=== Response written to ${RESPONSE_DIR}/response.txt ==="
