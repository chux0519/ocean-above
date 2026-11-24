# 09-stop — acceptance (2026-11-24)

Scope: #48, user Stop of a running agent turn. Plan: [plan.md](plan.md); details: [STATE.md](STATE.md).

- **Built (PR #54):** app-owned `run_id` (header + first SSE event), in-process run registry (single worker,
  documented), `POST /api/conversations/{id}/runs/{run_id}/cancel` (owner-checked, idempotent, an old run never stops
  a newer one), terminal `cancelled` event, `Message.status`, app.db schema v4. Stopped turns keep only guarded text and
  finished cards, stored not presented (no nonce) — a stopped turn mints no booking authority (decision 1, rule R6).
  Frontend Stop button, "Stopped" marker distinct from errors, authoritative reload, no stale updates after switching
  conversations. Invariants R1–R8 in `docs/design/state_invariants.md`; contract v1.6.
- **Race tests:** every boundary from the issue has a named test (`backend/tests/test_stop.py`, table in STATE);
  B1–B7 model test extended with stopped-turn quotes.
- **Independent review (Fable):** [review-stop.md](review-stop.md) — no P0/P1, 9 P2, all fixed; the two reproduced
  by probe (Stop during the `server_busy` refund ended the stream without a terminal event; finished runs kept their
  whole context in the registry) have named regression tests. Mutation checks: backend 9/9 and frontend 9/9 caught.
  Two real bugs found on the way: a slow reload after switching conversations overwrote the new view; an interrupted
  tool was recorded as "not run".
- **Tests (merged as `9eaf474`, verified by the coordinator):** backend 902 passed, `tool/test_diff.sh` no new
  failures, lint clean, vitest 103 passed, evals fake 31/31.
- **Real model (deepseek-flash, high, N=1):** 31/31, 0 safety violations, $0.0219 before review fixes; 31/31, $0.0223
  after.
- **Screenshots:** 1440 and 390, light and dark, waiting for the first token / stopped reply / next message
  (`docs/iterations/09-stop/`, local dev server).
- **Released** `43a72de` (api + web), production API Stop check passed (docs/deploy.md). Observed: the two other
  route tools of the same completion had already finished server-side before the Stop request arrived (tools take
  milliseconds), so three cards were stored — all not bookable, as specified.
- **Docs:** design.md §4.7, §4.10, §6, §7; DESIGN.md composer; README; deploy.md schema v4 rollback; release script now
  backs up the WAL files too.

Not done: browser check of the Stop button on production (covered by local screenshots + production API check).
