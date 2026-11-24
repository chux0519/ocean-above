# Ocean Above

> **TL;DR**
> - **What:** a chat app that books Ocean Above flights from natural language; code (not the LLM) computes routes and prices, and only your click books.
> - **Demo:** https://ocean-above.potafree.net — sign in with Google or an invite code (reviewers get theirs privately).
> - **Run locally:** `cp .env.example .env` (add an LLM key), `mkdir -p locations && cp locations.db locations/`, `docker compose up -d --build` → http://localhost:3000 ([details](#run-locally)).
> - **Design doc:** [DESIGN.md](DESIGN.md) (its TL;DR fits on one screen). Raw agent sessions: [`prompt history/`](prompt%20history/).

Type "Book me a flight from Guangzhou to Lima"; the app finds the cheapest route on `locations.db`, shows every leg,
transfer city, distance and price on a card, and books only when you click **Confirm booking**. The take-home brief is
`Quotr Agent Dev Engineer take-home.md`.

## Try the demo

https://ocean-above.potafree.net

- **Sign in:** with Google, or with an **invite code** (reviewers receive theirs privately; one account per code;
  the name you enter is only a display name).
- Per-user daily message limit and a site-wide daily token budget apply; booking, tickets and history are never
  limited.
- Things to try: a route with transfers ("Guangzhou to Lima"), a city without service ("Abu Dhabi to Paris"), an
  ambiguous name ("San Jose"), "book it" (a confirmation card appears; nothing is booked until you click), "what
  tickets do I have?", the conversation list, and **Stop** while a reply is streaming.

## Run locally

Requirements: Docker with Compose v2.24+ (or OrbStack / Docker Desktop), and an OpenAI-compatible LLM key.

```bash
cp .env.example .env                      # then fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, DEV_AUTH_USER, DEV_AUTH_PASSWORD
mkdir -p locations && cp locations.db locations/   # the API mounts this directory read-only; the copy is not versioned
docker compose up -d --build
curl http://127.0.0.1:3000/health         # through the proxy; OCEAN_PORT in .env changes the port
# then open http://localhost:3000 and sign in with DEV_AUTH_USER / DEV_AUTH_PASSWORD
docker compose down
```

- Locally you sign in with the fixed development account from `.env` (no Google needed). `INVITE_CODES` and
  `GOOGLE_CLIENT_ID` enable the other login methods.
- Services: `proxy` (Caddy, the only published port, bound to `127.0.0.1`), `web` (Next.js), `api` (FastAPI, one worker).
  `/api/*` and `/health` go to the API with no buffering (SSE); everything else goes to the web app.
- `./data/` holds the application database (`app.db`) and survives rebuilds; delete it only to reset state.
- After replacing `locations.db` on the host, copy the new file into `./locations/` again; the API reloads it on the
  next request because the fingerprint changed (no restart needed). Use `LOCATIONS_DIR=/path/to/dir` to mount another directory.
- Logs: `docker compose logs -f api` (json-file, 10 MB × 3 per service).

### Without Docker

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python and the dependencies from `backend/uv.lock`) and
Node.js LTS.

```bash
cd backend
uv sync                                               # runtime: fastapi, uvicorn, networkx, pydantic-ai-slim (agent runtime, ADR-001); dev: pytest, hypothesis, httpx
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health                     # {"status":"ok","network":{"cities":711,...,"fingerprint":"sha256:..."}}

cd ../frontend
npm ci && npm run dev                                 # http://localhost:3100 (PORT changes it); /api is forwarded to API_ORIGIN (default 127.0.0.1:8000)
```

`locations.db` in the repository root is read by default; set `LOCATIONS_DB=/path/to/locations.db` to use another
file. The database is opened read-only and validated at startup: a malformed database stops the server instead of
being repaired silently. Settings come from the environment, then from `.env` in the repository root (or the file
named by `OCEAN_ENV_FILE`).

## Tests, evals, benchmarks

```bash
# backend (from backend/)
uv run pytest -q                        # whole suite, about 40 s
uv run pytest -q -m "not slow"          # skips the all-pairs cross-check (a few seconds)
uv run pytest -q -s -m slow             # only the all-pairs cross-check, prints coverage and time

# frontend (from frontend/)
npm test                                # vitest

# from the repository root
bash tool/test_diff.sh                  # only new failures count (HYPOTHESIS_PROFILE=ci: deterministic examples)
python3 tool/review_lint.py             # review rules

# LLM evals (from backend/)
uv run python -m evals --mode fake                    # 45 multi-turn cases against a scripted model; free, deterministic
uv run python -m evals --mode real --estimate         # cost estimate for the real model
uv run python -m evals --mode real --n 1 --out /tmp/evals   # real model from .env; refuses above --max-cost (default $1)

# app.db concurrency benchmark (from backend/; no LLM cost)
nice -n 10 uv run python -m bench                     # --quick for a smoke run
```

### Multi-model benchmark (optional, costs money)

Compare models and providers on the same eval cases. A spec in `backend/evals/experiments/` lists configurations
(provider, model, dialect, reasoning effort; credentials as environment variable names only). Prices come from
`backend/evals/pricing.json`; a model without a known price (e.g. a token plan) is compared by tokens and its cost is
reported as `unknown`.

```bash
cd backend
PYTHONPATH=. uv run python -m evals.bench --spec 12-bench.json --fake --n 1          # harness check, no network
OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals.bench --spec 12-bench.json --estimate
OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals.bench --spec 12-bench.json --smoke   # 3 cases, n=1
OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals.bench --spec 12-bench.json --n 3 \
    --max-cost 0.30 --max-tokens 5000000 --out ../docs/iterations/12-bench/results/n3
```

Output: `bench.md` (comparison matrix, per-case table, failed-case drill-down), `bench.json` (every run) and one
full eval report per configuration. Pass/fail comes from the deterministic checks and the safety invariants only;
with small N, compare the confidence intervals, not the point values. Production is unaffected: it keeps
`deepseek-flash` with the DeepSeek dialect (`LLM_DIALECT` unset).

What the tests cover, how the route numbers are cross-checked against an independent reference implementation on
all 490,700 served-city pairs, and the real-model results so far: [DESIGN.md §3.5 and §6](DESIGN.md#6-how-it-was-built-and-tested).

What is in `backend/app/core/` (pure, deterministic code; no LLM, no HTTP):

| Module | Role |
|---|---|
| `network.py` | Loads `locations.db` read-only, validates it, builds an immutable `Network` with a content fingerprint (`sha256:…`). `NetworkProvider.current()` reloads when the file changes. |
| `pricing.py` | `price_path(net, cities)`: the one canonical distance and price function (Decimal, 50 digits, explicit context; values stored to 30 decimal places). `fmt_km` / `fmt_usd` round for display only. |
| `routing.py` | `cheapest_route(net, origin, destination)`: cheapest route with transfers only at hubs, or a typed error (`unknown_city`, `ambiguous_city`, `same_city`, `no_service`, `unreachable`). |
| `places.py` | `resolve` (exact, then case- and accent-insensitive; never picks between look-alike names such as `San Jose` / `San José`) and `nearest_served`. |

## Promptfoo view (optional)

The same benchmark can be browsed in [Promptfoo](https://promptfoo.dev): one row per eval case, one column per
configuration, each cell the whole conversation (turns, tool calls, cards, clicks) with two deterministic
assertions, `safety` and `trajectory`. It is optional and isolated in `evals/promptfoo/` (its own `package.json`);
the app and the test suite do not depend on it. Telemetry, update checks and sharing are off; results stay in
`evals/promptfoo/.promptfoo/`.

```bash
cd evals/promptfoo && npm ci                     # once (pinned promptfoo)
cd backend && uv sync                            # once (the provider runs with the backend's Python)
bash evals/promptfoo/pf.sh eval -c promptfooconfig.fake.json      # scripted model, no network, no cost
OCEAN_ENV_FILE=/path/to/.env bash evals/promptfoo/pf.sh eval -c promptfooconfig.json \
    --filter-providers '^deepseek-official$'     # real model; per-configuration cost / token guards
bash evals/promptfoo/pf.sh view                  # opens the comparison at http://localhost:15500
```

Regenerate the configs after changing cases or the spec: `backend/.venv/bin/python evals/promptfoo/gen.py --fake
--out promptfooconfig.fake.json && backend/.venv/bin/python evals/promptfoo/gen.py`.

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

## Repository map

| Path | What |
|---|---|
| `backend/app/core/` | deterministic routing, pricing, city resolution on `locations.db` |
| `backend/app/airline/` | quotes, confirmation cards and nonces, booking with re-check (B1–B7), ticket lookup |
| `backend/app/agent/` | turn loop on Pydantic AI Slim, the four tools, ReplyGuard, budgets, context, Stop |
| `backend/app/api/` | FastAPI routes, SSE, login (dev account, invite codes, Google ID token), limits |
| `backend/app/store/` | app.db (SQLite, no ORM) |
| `backend/tests/` | unit, property, model-based, replay and API tests; `reference.py` is the independent route implementation |
| `backend/evals/` | LLM eval suite (fake and real model) |
| `backend/bench/` | app.db concurrency benchmark |
| `frontend/` | Next.js app; UI design system in [frontend/DESIGN.md](frontend/DESIGN.md) |
| `contract/fixtures/` | example API and SSE payloads loaded by both sides' tests |
| `deploy/` | Caddyfile and the release script |
| `docker-compose.yml` | proxy + web + api (+ optional Phoenix) |
| `spikes/` | proof-of-concept code for the agent-runtime decision (Pydantic AI vs OpenAI Agents SDK); evidence for ADR-001, not used by the app |
| `prompt history/` | raw coding-agent sessions, unedited except for removed secrets |
| `docs/` | design notes, API contract, deploy log, roadmap, per-iteration plans and acceptance notes |
| `tool/` | lint, test-diff, model-check helper, session export, worker launcher |
| `AGENTS.md`, `PRODUCT.md` | working rules for the coding agents; product brief |

## Documentation index

| Document | Content |
|---|---|
| [DESIGN.md](DESIGN.md) | the take-home design doc (English) |
| [docs/design/design.md](docs/design/design.md) | detailed working notes (Chinese), the full decision record |
| [docs/design/adr-001-agent-runtime.md](docs/design/adr-001-agent-runtime.md) | agent runtime decision with measurements |
| [docs/design/state_invariants.md](docs/design/state_invariants.md) | booking, quota, concurrency and Stop invariants |
| [docs/design/sqlite-concurrency.md](docs/design/sqlite-concurrency.md) | SQLite limits, benchmark, PostgreSQL criteria |
| [docs/api/contract.md](docs/api/contract.md) | HTTP, SSE and card contract (v1.7) |
| [docs/deploy.md](docs/deploy.md) | deployment and release log |
| [docs/roadmap.md](docs/roadmap.md) | post-MVP decisions and order of work |
| [docs/review_rules.md](docs/review_rules.md) | review rules (checked by `tool/review_lint.py`) |
| `docs/iterations/<id>/` | plan, state and acceptance of each iteration |
