#!/usr/bin/env bash
#
# Full Locust load test against a locally running AgentWatch API.
#
# Prerequisites:
#   pip install locust --break-system-packages
#   # start the API first, e.g.:
#   uvicorn panoptis.api.server:app --host 0.0.0.0 --port 8000
#
# Results are written to tests/load/results_*.csv
#
# Targets: p95 < 500ms, failure rate < 1%.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

locust -f "${SCRIPT_DIR}/locustfile.py" \
  --host http://localhost:8000 \
  --users 500 \
  --spawn-rate 50 \
  --run-time 60s \
  --headless \
  --csv "${SCRIPT_DIR}/results"
