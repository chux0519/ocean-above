# Deploy — demo `ocean-above.potafree.net`

Agreed with the homelab agent ("potafree.net", hc machine `homelab`) on 2026-11-23.

## Host
- linux/amd64, Docker Engine 29.x, Docker Compose v5.x. Plenty of RAM; measure after the first deploy.
- Checkout: `git clone` of the private GitHub repo into `~/llc2026/ocean-above` on the homelab (`gh` is logged in
  there). Fallback: a code bundle without secrets sent over hc.
- Public entry: Caddy (`proxy` service) published on **`127.0.0.1:18765`** (`OCEAN_PORT=18765`; host port 3000 is
  taken). A placeholder `/health` service currently holds that port; the homelab agent stops it when compose
  takes over.
- Tunnel: a dedicated remotely-managed tunnel `ocean-above-homelab` in the Cloudflare account that owns
  `potafree.net`, routing `ocean-above.potafree.net` → `http://127.0.0.1:18765`.
  - Status 2026-11-23: DNS edit on `potafree.net` confirmed (homelab created and removed a test TXT record); the
    homelab agent will add the CNAME. **Blocked:** creating the tunnel returns 403 with the current token. Needs
    `Account › Cloudflare Tunnel › Edit` on that account, or the account admin creates the tunnel and puts the
    connector credentials on the homelab via a secure channel. Not needed before iteration 04.

## What we hand over for each deploy
- Repo URL, branch or commit.
- `locations.db` source (in the repo) and its read-only mount path in the `api` container.
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
