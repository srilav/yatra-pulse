#!/usr/bin/env bash
# Record the key scenarios locally and keep the evidence in deploy/local/results/.
set -uo pipefail
cd "$(dirname "$0")/../.."
R=deploy/local/results
mkdir -p "$R"
.venv/bin/python tests/run_dashboard.py --hold 3 --pause 1 --record "$R/key-scenarios.mp4" --report "$R/selenium.json"
sel=$?
if kind get clusters 2>/dev/null | grep -qx yatra-pulse; then ./deploy/local/smoke-test.sh | tee "$R/smoke.txt"; fi
[ $sel -eq 0 ]
