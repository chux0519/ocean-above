#!/bin/bash
# Runs every test suite that exists and prints one normalized line per failure: "FAIL <suite>: <test name>".
# Used by tool/test_diff.sh. Backend: pytest (FastAPI). Frontend: vitest (Next.js).
set -uo pipefail
cd "$(dirname "$0")/.."
status=0
if [ -d backend ]; then
  (cd backend && uv run pytest -q --tb=short -rf) 2>&1 | tee /dev/stderr \
    | sed -nE 's/^FAILED ([^ ]+)( - .*)?$/FAIL backend: \1/p'
  [ "${PIPESTATUS[0]}" -ne 0 ] && status=1
fi
if [ -d frontend ] && [ -f frontend/package.json ]; then
  (cd frontend && npx vitest run) 2>&1 | tee /dev/stderr \
    | sed -nE 's/^ *(FAIL|×) +(.*[^ ])( [0-9]+ms)?$/FAIL frontend: \2/p'
  [ "${PIPESTATUS[0]}" -ne 0 ] && status=1
fi
exit $status
