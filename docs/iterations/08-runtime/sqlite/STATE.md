# 08-runtime / sqlite (#41) — STATE

Branch `it08-sqlite`, worker task S of `docs/iterations/08-runtime/plan.md`. No database migration, no change to
booking logic or transaction boundaries; B1–B7 unchanged.

## Done
| Deliverable | Where |
|---|---|
| Design section draft (English): WAL / single writer, per-conversation lock (in-process), single worker, `_recheck` inside the write transaction (+ candidate optimisation and TOCTOU analysis, **not implemented**), SQLite lock wait vs `conversation_busy` vs `server_busy`, why it is a conscious trade-off, benchmark, measured results, decision table (thresholds marked as example SLOs) | `docs/design/sqlite-concurrency.md` (merged) |
| Timing hook: `app.store.db.set_timing_hook` / `OpTiming` — BEGIN wait, hold to COMMIT/ROLLBACK, total, outcome (commit / ok / rollback / busy / timeout / error). Off by default (no clock read at all when off) | `backend/app/store/db.py` |
| Hook tests: off by default; the same scenario (commit, rollback, SQL error, lock-wait timeout, operation deadline) gives identical results with the hook on and off; spans and outcomes; wait measures a real lock; a raising hook changes nothing; no clock reads when off | `backend/tests/test_store_timing.py` |
| Benchmark harness, no new dependencies (httpx + asyncio, uvicorn subprocess, fake LLM, isolated app.db + locations.db copy per level, fixed seed, integrity checks) | `backend/bench/` |
| Tiny end-to-end benchmark run in the test suite (~9 s) | `backend/tests/test_bench.py` |
| Measured run on this machine (285 s wall, `nice -n 10`) | `results/bench-2026-11-24.{json,md}` |

## Key results (details: design-section.md X.5, results/)
- Integrity: 0 failures everywhere (double bookings, status mismatches, interleaved turns); 0 BUSY, 0 5xx.
- Write hold p50 0.5–1.6 ms, p99 ≤ 34 ms; the tail is SQLite busy-handler *waiting* (p99 up to 0.58 s at 25 clients).
- Single worker CPU saturates first (~200 req/s peak at 50 closed-loop clients).
- A `locations.db` rebuild inside the booking transaction holds the write lock ~140 ms and raises other writers' wait p99 to 2.8 s.
- With 0.6 s simulated LLM latency per step the 8 turn slots (`server_busy`) bind long before app.db.
- Decision: keep SQLite + one worker for the demo; ordered next steps in the decision table.

## Real-model cost
$0 — no real model was called (fake LLM only; budget was $0.15).

## For the coordinator
- Merge `docs/design/sqlite-concurrency.md` (merged) into `docs/design/design.md` (new section after §4.9 or in §11); §11's line
  "单进程、单 worker、SQLite；并发量有限，demo 足够" can link to it.
- Issue #41 says `timeout=30`; the code uses `BUSY_TIMEOUT = 10` s (since 06 P0b). The draft uses 10 s.
- Files outside the plan's ownership list (tests only): `backend/tests/test_store_timing.py`, `backend/tests/test_bench.py`.
- The machine ran other agents during the benchmark (load average ~2 before start); numbers ±30 %.
- Candidate optimisations (not implemented, each needs B1–B7 review): re-check before the write lock with an
  in-transaction fingerprint compare (X.3); in-process FIFO writer lock in front of `BEGIN IMMEDIATE`.

## How to verify
```
cd backend
HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_store_timing.py tests/test_bench.py tests/test_booking_model.py
nice -n 10 uv run python -m bench --quick          # seconds; prints the results path
nice -n 10 uv run python -m bench                  # ~5 min; rewrites results/bench-<date>.*
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
```

## Tested source state
See the PR head commit; test_diff / lint results recorded in the PR description.
