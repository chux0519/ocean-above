# Deploy — `ocean-above.potafree.net`

## Production: VPS (since 2026-11-23)
Migrated by the user's codex agent (report received over hc, 2026-11-23). Source of truth for releases from now on.
- Host: a small RackNerd VPS (about 1 GiB RAM; containers use about 215 MiB). Docker Engine 29.x, Compose v5.x,
  compose project `ocean-above`, checkout `~/llc2026/ocean-above`, existing `.env` (600), `data/app.db`, `locations/`.
- Edge: Cloudflare proxied A record → nginx on the VPS (Let's Encrypt, auto-renew tested) → `127.0.0.1:18765` (Caddy
  `proxy` service) → `web` / `api`. The homelab tunnel `ocean-above-homelab` was deleted. With Cloudflare in front,
  `TRUST_CF_CONNECTING_IP=1` must stay set; nginx must pass `CF-Connecting-IP` / `X-Forwarded-For` through.
- **Build elsewhere, never on the VPS** (too little RAM): build `linux/amd64` images on the build machine, ship code as a
  git bundle (private repo; GitHub read access from the VPS not verified) and images with `docker save | ssh … docker
  load`, then on the VPS: `sudo -n docker compose -p ocean-above up -d --no-build --pull never`. Reuse the SSH
  ControlMaster (from the homelab: `~/.ssh/cm-homelab-monitoring-racknerd`).
- Verified at `main@21d25e4`: home page, `/api/health`, `/api/debug/sse-ping` (first event ≈ 1.15 s), SQLite snapshot
  checksum, locations fingerprint, Google JWKS and DeepSeek reachable, certificate renewal dry run.
- Every release: `deploy/release_vps.sh` on the build machine (homelab: `SSH_CONTROL=~/.ssh/cm-homelab-monitoring-racknerd
  BUILD_PROXY=http://192.168.1.13:7890`), then record commit and checks in the deploy log below. app.db is copied to
  `/tmp/app.db.pre-<commit>` on the VPS before each release.

## Homelab (previous demo host, retired as the public entry 2026-11-23)
- 2026-11-24: compose project `ocean-above` stopped on the homelab (`docker compose down`, user decision); `data/` kept.
  The homelab remains the image build machine for VPS releases.

Agreed with the homelab agent ("potafree.net", hc machine `homelab`) on 2026-11-23.

## Host
- linux/amd64, Docker Engine 29.x, Docker Compose v5.x. Plenty of RAM; measure after the first deploy.
- Checkout: `git clone` of the private GitHub repo `git@github.com:chux0519/ocean-above.git` (default branch `main`) into `~/llc2026/ocean-above` on the homelab (`gh` is logged in
  there). Fallback: a code bundle without secrets sent over hc.
- Public entry: Caddy (`proxy` service) published on **`127.0.0.1:18765`** (`OCEAN_PORT=18765`; host port 3000 is
  taken). A placeholder `/health` service currently holds that port; the homelab agent stops it when compose
  takes over.
- Tunnel: a dedicated remotely-managed tunnel `ocean-above-homelab` in the Cloudflare account that owns
  `potafree.net`, routing `ocean-above.potafree.net` → `http://127.0.0.1:18765`.
  - Status 2026-11-23: **live.** Tunnel `ocean-above-homelab` created in the `potafree.net` account, proxied CNAME
    in place, connector enabled at boot (4 active connections). `https://ocean-above.potafree.net/health` returns 200
    from the placeholder service on `127.0.0.1:18765` (TLS verified). Compose's Caddy takes over the same port.

## Building on the homelab
- Docker Hub is not reachable directly. The daemon already uses the local proxy for pulls; the **build** needs it too,
  in the same shell: `export HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=$HTTP_PROXY http_proxy=$HTTP_PROXY
  https_proxy=$HTTP_PROXY`, then `docker compose -p <name> build --builder default` and `up -d --no-build`.
  Fallback mirrors: USTC or Aliyun Docker Hub mirrors.
- The web build fetches Public Sans from Google Fonts (`next/font/google`). Inside the build container `127.0.0.1` is
  the container itself, so pass the LAN proxy as build args: `--build-arg HTTP_PROXY=http://192.168.1.13:7890
  --build-arg HTTPS_PROXY=http://192.168.1.13:7890 --build-arg NO_PROXY=localhost,127.0.0.1`.
- 2026-11-23: compose skeleton (PR #9) verified there: build OK, `/health` 200 through Caddy, `/` → `/login` 200.

## Deploy log
- 2026-11-24 `4b667ed`: 07-ui soft-minimalist redesign (web image; api unchanged) via `deploy/release_vps.sh`. Public
  check with ego-browser (invite test code): "Book me a flight from Guangzhou to Lima" → quote + confirmation card,
  $1,160.71; light 1440 and dark 390 screenshots in `docs/iterations/07-ui/e2e/`, no horizontal scroll.
- 2026-11-24 `2f6f13e`: first coordinator release to the VPS (06 hardening: budgets, concurrency, runtime fixes; api
  image only). Public `/api/health` 200, `/api/config` (dev off, invite + Google on), SSE first event 0.78 s then every
  2 s, live quote Shanghai → Beijing $1,810.21. Invite code 3 rotated after the check; a separate coordinator test code
  exists (not for reviewers).
- 2026-11-23 `21d25e4`: moved to the VPS (see top). Homelab compose no longer receives public traffic.
- 2026-11-23 `bb4488e`: first real deploy (compose project `ocean-above`, `OCEAN_PORT=18765`, `.env` placed by scp,
  mode 600). Public `/api/health` 200; SSE ping through Cloudflare: first event 1.34 s, then every 2 s (no buffering).
  Dev fixed-account login is on (strong random password, not public); Google login + limits before announcing (05).
- 2026-11-23 `6659d78`: 04-web (Timetable design, light/dark) deployed; web image rebuilt with the build-arg proxy.
- Redeploy: `cd ~/llc2026/ocean-above && git pull --ff-only` → build with the proxy env (above) →
  `OCEAN_PORT=18765 docker compose -p ocean-above up -d --no-build`.

## What we hand over for each deploy
- Repo URL, branch or commit.
- `locations.db` (and any other db file) is copied to the homelab with `scp`, into the directory mounted read-only
  in the `api` container; `data/app.db` stays on the homelab volume and is never overwritten by a deploy.
- `.env.example` (variable names only). Secrets reach the homelab via a secure channel, never in hc messages.
- An SSE route for acceptance (the homelab agent checks that the first event arrives promptly through Caddy).

## Compose requirements
- Services `web` (Next.js :3000), `api` (FastAPI :8000), `proxy` (Caddy; `/api/*` → api with no buffering for SSE,
  the rest → web). Only `proxy` publishes a port, on loopback: `127.0.0.1:${OCEAN_PORT:-3000}`.
- Volume `./data` for `app.db`; the **directory** holding `locations.db` mounted read-only (a single-file bind
  mount does not see a replaced file, which would break fingerprint reloads).
- `api` runs a single uvicorn worker (per-conversation locks are in-process).
- Frontend config is read at runtime from `/api/config` (one image for local and demo; no build-time client id).
- SSE: Caddy `flush_interval -1`; the app sends a `ping` every 15 s so idle streams are not cut.
- Cookies: `Secure` controlled by env (`COOKIE_SECURE=1` on the demo, behind Cloudflare HTTPS).
- Logs to stdout/stderr; every service uses the `json-file` log driver with rotation `max-size: 10m`, `max-file: 3`.
- `restart: unless-stopped`.

## Before the demo goes public (iteration 05)
- `.env` on the homelab: `TRUST_CF_CONNECTING_IP=1` (required behind Cloudflare, otherwise every client shares one
  throttle key), `GOOGLE_CLIENT_ID`, `INVITE_CODES` (**one code per reviewer**), `DAILY_MESSAGES_PER_USER`,
  `DAILY_TOKEN_BUDGET`, `HTTPS_PROXY=http://192.168.1.13:7890` + `NO_PROXY=localhost,127.0.0.1,api,web` (the api
  container fetches Google's JWKS); remove `DEV_AUTH_USER` / `DEV_AUTH_PASSWORD`.
- Renaming or removing the dev user does not end its existing sessions: rotate `SESSION_SECRET` when disabling it.
- Google login works; OAuth app published to **Production**; invite-code login for reviewers.
- Per-user daily message cap and global daily budget enabled; spend cap set at the LLM provider.
- Dev fixed-account login disabled on the demo.
