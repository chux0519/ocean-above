#!/bin/bash
# Runs the pinned local Promptfoo (#47 B2) with telemetry, update checks, sharing and remote
# generation off, its database inside this folder (.promptfoo/, git-ignored) and the backend's Python.
#   bash evals/promptfoo/pf.sh eval -c promptfooconfig.fake.json      # zero cost
#   OCEAN_ENV_FILE=/path/to/.env bash evals/promptfoo/pf.sh eval -c promptfooconfig.json --filter-providers deepseek-official
#   bash evals/promptfoo/pf.sh view                                    # local web UI
set -euo pipefail
cd "$(dirname "$0")"
export PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_DISABLE_SHARING=1 \
  PROMPTFOO_DISABLE_REMOTE_GENERATION=1 PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=1 \
  PROMPTFOO_DISABLE_SHARE_EMAIL_REQUEST=1 PROMPTFOO_CACHE_ENABLED=false
export PROMPTFOO_CONFIG_DIR="$PWD/.promptfoo"
export PROMPTFOO_PYTHON="$PWD/../../backend/.venv/bin/python"
[ -x "$PROMPTFOO_PYTHON" ] || { echo "run 'cd backend && uv sync' first" >&2; exit 2; }
[ -x node_modules/.bin/promptfoo ] || { echo "run 'npm ci' in evals/promptfoo first" >&2; exit 2; }
exec node_modules/.bin/promptfoo "$@"
