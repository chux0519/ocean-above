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
  `potafree.net`, routing `ocean-above.potafree.net` → `http://127.0.0.1:18765`. **Blocked:** the current token
  cannot create tunnels in that account (403). Needs `Account › Cloudflare Tunnel › Edit` on that account (DNS
  Edit for the zone already works), or the account admin creates the tunnel and puts the connector credentials on
  the homelab via a secure channel.

## What we hand over for each deploy
- Repo URL, branch or commit.
- `locations.db` source (in the repo) and its read-only mount path in the `api` container.
- `.env.example` (variable names only). Secrets reach the homelab via a secure channel, never in hc messages.
- An SSE route for acceptance (the homelab agent checks that the first event arrives promptly through Caddy).

## Compose requirements
- Services `web` (Next.js :3000), `api` (FastAPI :8000), `proxy` (Caddy; `/api/*` → api with no buffering for SSE,
  the rest → web). Only `proxy` publishes a port, on loopback: `127.0.0.1:${OCEAN_PORT:-3000}`.
- Volume `./data` for `app.db`; `locations.db` mounted read-only.
- Logs to stdout/stderr; every service uses the `json-file` log driver with rotation `max-size: 10m`, `max-file: 3`.
- `restart: unless-stopped`.
