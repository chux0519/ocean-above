# Deploy — demo `ocean-above.potafree.net`

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
- 2026-11-23: compose skeleton (PR #9) verified there: build OK, `/health` 200 through Caddy, `/` → `/login` 200.

## Deploy log
- 2026-11-23 `bb4488e`: first real deploy (compose project `ocean-above`, `OCEAN_PORT=18765`, `.env` placed by scp,
  mode 600). Public `/api/health` 200; SSE ping through Cloudflare: first event 1.34 s, then every 2 s (no buffering).
  Dev fixed-account login is on (strong random password, not public); Google login + limits before announcing (05).
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
- Google login works; OAuth app published to **Production**; invite-code login for reviewers.
- Per-user daily message cap and global daily budget enabled; spend cap set at the LLM provider.
- Dev fixed-account login disabled on the demo.
