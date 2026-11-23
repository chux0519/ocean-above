#!/bin/bash
# Release merged `main` to the production VPS. Run on the BUILD machine (not the VPS: too little RAM).
#   deploy/release_vps.sh [services...]     default: api web
# Env: VPS_SSH (ssh target, default "racknerd"), SSH_CONTROL (ControlPath to reuse), BUILD_PROXY (http proxy for
# image builds, empty = none), VPS_DIR (checkout on the VPS, default ~/llc2026/ocean-above).
# Steps: pull main → build linux/amd64 images → ship the commits as a git bundle → back up app.db → ship images with
# docker save | docker load → up -d --no-build --pull never → local health check. Log the result in docs/deploy.md.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICES=${*:-api web}
VPS_SSH=${VPS_SSH:-racknerd}; VPS_DIR=${VPS_DIR:-'~/llc2026/ocean-above'}
SSH_OPTS=(-o BatchMode=yes); [ -n "${SSH_CONTROL:-}" ] && SSH_OPTS+=(-o "ControlPath=$SSH_CONTROL")
R() { ssh "${SSH_OPTS[@]}" "$VPS_SSH" "$@"; }
git fetch -q origin && git checkout -q main && git pull -q --ff-only
NEW=$(git rev-parse --short HEAD); echo "release $NEW ($SERVICES)"
ARGS=(); if [ -n "${BUILD_PROXY:-}" ]; then
  ARGS=(--build-arg "HTTP_PROXY=$BUILD_PROXY" --build-arg "HTTPS_PROXY=$BUILD_PROXY" --build-arg NO_PROXY=localhost,127.0.0.1)
fi
# shellcheck disable=SC2086
docker compose -p ocean-above build --builder default "${ARGS[@]}" $SERVICES
OLD=$(R "cd $VPS_DIR && git rev-parse HEAD")
if [ "$(git rev-parse HEAD)" != "$OLD" ]; then
  B=$(mktemp); git bundle create "$B" "$OLD..main"
  R "cat > /tmp/oa.bundle && cd $VPS_DIR && git fetch -q /tmp/oa.bundle main && git merge -q --ff-only FETCH_HEAD && rm -f /tmp/oa.bundle" < "$B"
  rm -f "${B:?}"
fi
R "cd $VPS_DIR && sudo -n cp data/app.db /tmp/app.db.pre-$NEW"
for s in $SERVICES; do docker save "ocean-above-$s" | gzip -1 | R "gunzip | sudo -n docker load"; done
R "cd $VPS_DIR && sudo -n docker compose -p ocean-above up -d --no-build --pull never && sleep 6 && curl -fsS -o /dev/null -w 'health %{http_code}\n' http://127.0.0.1:18765/api/health && git log --oneline -1"
