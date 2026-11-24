"""app.db / SQLite concurrency benchmark (#41). No real LLM, no network, no new dependencies.

Run from backend/ (one run, ~10 minutes on a laptop-class machine):

    nice -n 10 uv run python -m bench                 # full sweep, writes docs/iterations/08-runtime/sqlite/results/
    nice -n 10 uv run python -m bench --quick         # smoke run (seconds), results to a temp dir

What it does: for every concurrency level it seeds a fresh, isolated app.db (users, conversations,
presented quotes with nonces) next to a private copy of locations.db, starts the real FastAPI app
in a separate uvicorn process (one worker, as deployed) with a deterministic fake LLM, drives it
over HTTP with httpx + asyncio, and collects server-side spans through `app.store.db`'s timing
hook and wrappers around `Airline._recheck` / `load_network` installed in the bench process only.
See `docs/iterations/08-runtime/sqlite/design-section.md` for what the numbers mean.
"""
