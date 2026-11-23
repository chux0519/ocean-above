# L2 usage limits — STATE (2026-11-23)

Branch `it05-limits`, issue #25. Tested source: the commit this file is in (see `git log -1`).

## Done
- `app/api/limits.py`: `reserve_message` (429 `rate_limited` with `scope` user / site + `resets_at`, contract v1.2 /
  `contract/fixtures/rate_limited.json` shape), `usage_view` for `/api/me.usage` (replaces the L1 stub), `resets_at`
  (next 00:00 UTC).
- `POST /api/conversations/{id}/messages`: order is 404 (ownership) → 503 (no LLM) → limits → stream. A refused message
  stores nothing (no message, no LLM call). A message the agent refuses as invalid (400) gives its slot back.
- Store schema **v2** (migration from v1 keeps data): table `message_usage` (one row per message let through) + index
  `llm_calls_by_time`. `Store.reserve_message` counts the user's rows today and the site's `SUM(llm_calls.tokens)` today
  and inserts the reservation in **one `BEGIN IMMEDIATE` transaction** → two racing messages cannot both take the last slot.
  Day = UTC day of the injectable store clock. NULL tokens count 0.
- Scope precedence: user limit reached → `scope: "user"`; else budget reached → `scope: "site"`.
- `Settings.daily_token_budget` (`DAILY_TOKEN_BUDGET`, default 2,000,000; `_` separators allowed); `ApiError(extra=…)`
  adds keys inside `error`.
- Booking, tickets, trace, history never call the limits (tested while the site budget is exhausted).

## Verify
```
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_limits.py tests/test_http_api.py tests/test_store.py
bash tool/test_diff.sh && python3 tool/review_lint.py
```
`tests/test_limits.py` (17): user limit 429 before SSE + nothing stored + model not called, other user unaffected, new
conversation doesn't reset, `/api/me.usage` shape (fixture) and count, bad text doesn't use a slot, 404/503 before 429,
rollover at 23:59:59 → 00:00:00 with injected clock (me.usage + resets_at move), resets_at in other zones / year end,
site budget across users + booking/tickets/history still work + next-day reset, scope precedence, NULL tokens,
**race**: 12 threads with separate connections at limit 1 and 3 → exactly `limit` pass; two concurrent HTTP requests for
the last slot → one 200, one 429; hypothesis model check against a per-user-per-day reference counter; v1→v2 migration;
env parsing. Mutation check: splitting count and insert into two transactions makes all 3 race tests fail.

## Known limits (by design, for the coordinator)
- The token budget is checked at message start; turns already running can overshoot it by their own cost (≤ a few
  turns × max 6 LLM calls). A hard cap would need per-call checks inside the agent (outside my scope).
- A message that gets `conversation_busy` (SSE error, another turn of the same conversation still running) has already
  used its slot. The UI disables send while streaming, so this needs two tabs.
- Proposed invariant for `docs/design/state_invariants.md` (coordinator owns it): "U1: for every user and UTC day,
  messages let through ≤ DAILY_MESSAGES_PER_USER; the count and the reservation are one write transaction; a refused
  message stores nothing." Model-based test: `test_reservations_match_a_reference_count`.
- `.env.example` already has the limit variables (commit 576b3f4).

## Open
- none.
