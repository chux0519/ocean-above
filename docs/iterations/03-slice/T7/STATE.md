# T7 — docker compose + README run section (issue #6)

Branch `it03-compose`. Model: Haiku 5.5 (mechanical wiring).

## Done
- `backend/Dockerfile`: `python:3.12-slim-bookworm`, uv 0.10.10 copied from its image, `uv sync --frozen --no-dev --no-install-project`,
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`, `LOCATIONS_DB=/srv/locations/locations.db`. `.dockerignore` added.
- `frontend/Dockerfile`: multi-stage on `node:22-bookworm-slim` (deps → build → run), `npm start` (`next start -H 0.0.0.0 -p $PORT`, PORT 3000),
  `API_ORIGIN=http://api:8000` at build and run time, runs as `node`. `.dockerignore` added.
- `deploy/Caddyfile`: `/api/*` and `/health` → `api:8000` with `flush_interval -1`; everything else → `web:3000`. Plain `:80`.
- `docker-compose.yml`: `web`, `api`, `proxy` (`caddy:2-alpine`, only published port `127.0.0.1:${OCEAN_PORT:-3000}:80`);
  `./data:/app/data`; `${LOCATIONS_DIR:-./locations}:/srv/locations:ro` (directory mount); `env_file: ./.env` (optional) for api;
  json-file `max-size 10m`, `max-file 3` on every service; `restart: unless-stopped`.
- `.env.example`: variable names only (LLM_BASE_URL LLM_API_KEY LLM_MODEL DEV_AUTH_USER DEV_AUTH_PASSWORD COOKIE_SECURE OCEAN_PORT).
- README: new "Run locally" section at the top.
- `.gitignore`: `/locations/` added. Reason: the `!locations.db` rule would otherwise un-ignore the runtime copy `locations/locations.db`.

## Deviation from the brief
- The `./locations` directory is filled by **copying** `locations.db` (README step), not by a symlink: a symlink target outside the mounted
  directory does not resolve inside the container. `LOCATIONS_DIR` can point at another directory. Copy is the same mechanism
  `docs/deploy.md` uses on the homelab (scp into the mounted directory).

## Verification
- **Docker was not verified.** No container runtime is installed on this Mac (no OrbStack/Docker app; `/usr/local/bin/docker` is a dangling symlink).
  Installing one is a global toolchain change, so it was not done. Not run: `docker compose build`, `up -d`, the proxy curls, `down`.
- `docker-compose.yml` parses as YAML; `api` has `env_file` optional, log options present on all 3 services, only `proxy` has `ports`.
- Backend as the Dockerfile runs it (locally, no container): `uv sync --frozen --no-dev --no-install-project`, then
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` with `LOCATIONS_DB=<repo>/locations.db` → `GET /health` 200
  (cities 711, hubs 40, hub_links 151, unserved 10).
- Frontend as the Dockerfile builds it: `npm ci` (clean), `npm run build` OK (routes `/`, `/chat`, `/history`, `/history/[id]`, `/login`, `/tickets`),
  `npm start` on port 3200 → `/` 307 to `/chat`, `/chat` 200, `/api/cities` through the Next rewrite → 200.
- Caddyfile not validated (no caddy binary here). Caddy's `path` matcher and `flush_interval -1` are standard syntax; still to check with `caddy validate`.
- Not checked: the SSE first-event timing through Caddy (`/api/debug/sse-ping` does not exist yet, T5 pending).

## Open decisions / next
- `docker compose up -d --build` on a machine with Docker, then `OCEAN_PORT=3300 curl 127.0.0.1:3300/health` and `/chat`. Run before merge.
- `api` writes `/app/data/app.db` by convention (mount exists). T5 must read the DB path from an env var (e.g. `APP_DB`) and default to `/app/data/app.db`.
- `web` image keeps devDependencies (no `npm prune`) so `next start` loads `next.config.ts` with no extra risk; can slim later.
- Login: `DEV_AUTH_*` are passed to `api` via `.env`; T5 reads them.

## How to verify (on a machine with Docker)
```bash
cp .env.example .env   # fill in values
mkdir -p locations && cp locations.db locations/
OCEAN_PORT=3300 docker compose up -d --build
curl -s http://127.0.0.1:3300/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3300/chat
docker compose down
```
