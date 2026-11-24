# Ocean Above

Natural-language flight booking for the airline Ocean Above. The agent's generic runtime (model streaming, tool
dispatch, cancellation) is Pydantic AI Slim ([ADR-001](docs/design/adr-001-agent-runtime.md)); booking, the reply
guard, history and budgets are our own code. Roadmap: [docs/roadmap.md](docs/roadmap.md).

Users can **Stop** a reply while it is generated: the server cancels the agent run (no further model or tool work)
and keeps only the text and cards produced so far, marked as stopped; nothing from a stopped reply can be booked.
Closing the tab does not stop a reply — it finishes and appears on reload. API: contract v1.6 in
[docs/api/contract.md](docs/api/contract.md).

## Run locally

Requirements: Docker with Compose v2.24+ (or OrbStack / Docker Desktop), and an OpenAI-compatible LLM key.

```bash
cp .env.example .env                      # then fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, DEV_AUTH_USER, DEV_AUTH_PASSWORD
mkdir -p locations && cp locations.db locations/   # the API mounts this directory read-only; the copy is not versioned
docker compose up -d --build
curl http://127.0.0.1:3000/health         # through the proxy; OCEAN_PORT in .env changes the port
# then open http://localhost:3000 in a browser
docker compose down
```

- Services: `proxy` (Caddy, the only published port, bound to `127.0.0.1`), `web` (Next.js), `api` (FastAPI, one worker).
  `/api/*` and `/health` go to the API with no buffering (SSE); everything else goes to the web app.
- `./data/` holds the application database (`app.db`) and survives rebuilds; delete it only to reset state.
- After replacing `locations.db` on the host, copy the new file into `./locations/` again; the API reloads it on the
  next request because the fingerprint changed (no restart needed). Use `LOCATIONS_DIR=/path/to/dir` to mount another directory.
- Logs: `docker compose logs -f api` (json-file, 10 MB × 3 per service).
- Without Docker: backend in `backend/` (section below), frontend in `frontend/` with `npm ci && npm run dev`.

## Backend development and tests

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python and the dependencies from `backend/uv.lock`).

```bash
cd backend
uv sync                                               # runtime: fastapi, uvicorn, networkx, pydantic-ai-slim (agent runtime, ADR-001); dev: pytest, hypothesis, httpx
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health                     # {"status":"ok","network":{"cities":711,...,"fingerprint":"sha256:..."}}
curl http://localhost:8000/api/cities                 # debug listing of the loaded network
```

`locations.db` in the repository root is read by default; set `LOCATIONS_DB=/path/to/locations.db` to use another
file. The database is opened read-only and validated at startup: a malformed database stops the server instead of
being repaired silently.

Tests:

```bash
cd backend && uv run pytest -q          # whole suite, about 40 s
uv run pytest -q -m "not slow"          # skips the all-pairs cross-check (a few seconds)
uv run pytest -q -s -m slow             # only the all-pairs cross-check, prints coverage and time
bash tool/test_diff.sh                  # from the repository root: only new failures count (runs with HYPOTHESIS_PROFILE=ci: deterministic examples)
python3 tool/review_lint.py             # from the repository root: review rules
```

What is in `backend/app/core/` (pure, deterministic code; no LLM, no HTTP):

| Module | Role |
|---|---|
| `network.py` | Loads `locations.db` read-only, validates it, builds an immutable `Network` with a content fingerprint (`sha256:…`). `NetworkProvider.current()` reloads when the file changes. |
| `pricing.py` | `price_path(net, cities)`: the one canonical distance and price function (Decimal, 50 digits, explicit context; values stored to 30 decimal places). `fmt_km` / `fmt_usd` round for display only. |
| `routing.py` | `cheapest_route(net, origin, destination)`: cheapest route with transfers only at hubs, or a typed error (`unknown_city`, `ambiguous_city`, `same_city`, `no_service`, `unreachable`). |
| `places.py` | `resolve` (exact, then case- and accent-insensitive; never picks between look-alike names such as `San Jose` / `San José`) and `nearest_served`. |

How the numbers are checked: `backend/tests/reference.py` is a second implementation that shares no code with
`app/` (its own database reader, graph, Decimal arithmetic and Bellman-Ford search). The test suite compares it
with the production code on every ordered pair of served cities in `locations.db` (490,700 pairs: price, distance
and city sequence must be identical) and on randomly generated small graphs, where an exhaustive enumeration of
all valid paths is the reference.

## Tracing (optional, off by default)

The API can export OpenTelemetry traces of each chat turn to a local [Phoenix](https://github.com/Arize-ai/phoenix):
the agent run, every model call (tokens, latency) and tool call, the route search, the reply check, a user's Stop and
the booking re-check. Spans carry codes, counts, durations and ids only — never message text, prompts, tool results,
city names, emails, nonces, cookies or keys.

With Docker:

    # .env
    OTEL_TRACING_ENABLED=1
    OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006

    docker compose --profile tracing up -d
    # chat at http://localhost:3000, traces at http://localhost:6006

A plain `docker compose up` does not start Phoenix, and without `OTEL_TRACING_ENABLED=1` nothing is traced or exported.
Running the backend on the host instead: start Phoenix (`docker compose --profile tracing up -d phoenix`, or
`pip install arize-phoenix && phoenix serve`) and set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006`. Evals
(`python -m evals`) honour the same variables. If the collector is down, spans are dropped; chat and booking are
unaffected.
