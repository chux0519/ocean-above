# Ocean Above

## Backend development and tests

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python and the dependencies from `backend/uv.lock`).

```bash
cd backend
uv sync                                               # runtime: fastapi, uvicorn, networkx; dev: pytest, hypothesis, httpx
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
