# Implementation — 03-slice T5 HTTP API (issue #4)

- Branch `it03-api`, based on main (02-core + T2). No new dependencies. Files: `backend/app/api/{settings,session,errors,routes}.py`,
  `backend/app/main.py`, `backend/tests/test_http_api.py` (+ `test_api.py` now passes a temp `app_db`).
- Done: `/api/health` (`/health` kept), `/api/config`, `POST /api/auth/dev`, `/api/auth/logout`, `/api/me`, conversations
  (list / create / detail), `/api/quotes/{id}/book` (`airline.book_async`, `BookingError` → contract code and status),
  `/trace`, `/api/tickets`, `/api/debug/sse-ping` (first ping immediate, then every 2 s, 5 total).
- **Not done:** `POST /conversations/{id}/messages` is a stub: login and ownership are checked (404), then `501 not_implemented`.
  Waiting for T3 (`it03-agent`) to merge.

## Decisions
- **Session cookie** `oa_session`: stateless `<user_id>.<expires>.<hmac>` (HMAC-SHA256, 7 days), httpOnly, SameSite=Lax,
  `Secure` from `COOKIE_SECURE`. The user row must still exist. `SESSION_SECRET` env keeps sessions across restarts; unset →
  random per process (a restart signs everyone out). Logout clears the cookie (no server-side revocation — fine for the dev login).
- Dev login is off unless both `DEV_AUTH_USER` and `DEV_AUTH_PASSWORD` are set (`config.dev_login=false`, login → 401
  `invalid_credentials`). Constant-time compare. The user is upserted as `("dev", DEV_AUTH_USER)`.
- Settings: `APP_DB` (default `backend/data/app.db`, git-ignored), `NONCE_SECRET`, `SESSION_SECRET`, `GOOGLE_CLIENT_ID`.
  `create_app(db_path, settings, clock)` — tests pass `Settings(...)`; the Store/Airline are created in the lifespan, so
  importing `app.main` touches no file.
- One `Airline` (hence one `ConversationLocks`) per process, in `app.state.airline`; T3's chat turn must use the same one.
- Errors: every failure is `{"error":{"code","message"}}`: validation → 400 `bad_request`, unknown route 404, wrong method 405,
  `NotFound` → 404, `BookingError` → its own status, `NetworkDataError` → 503 `data_unavailable`.
- `/api/debug/sse-ping` needs no login (it is for checking the proxy does not buffer SSE).
- Sync routes run in the thread pool (store calls block); `book` and conversation creation use threads explicitly.

## Tests (`tests/test_http_api.py`, 27) — full suite 289 passed
Every endpoint; 401 for each protected endpoint and for forged / expired / unsigned-user cookies; cross-user 404 on
conversation, messages, trace and book (and Alice's quote is untouched, later bookable by Alice); each booking error code
through HTTP (`invalid_nonce`, `not_presented`, `expired`, `stale`, `already_booked`, `data_unavailable`, `conversation_busy`,
`not_found`); response key sets validated against `contract/fixtures` (`assert_shape` from T2's helpers: cards, messages, trace,
error body, summaries, me, config, tickets, booking_ok).

## Verify
`cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q`; `bash tool/test_diff.sh`; `python3 tool/review_lint.py`.
Manual: `DEV_AUTH_USER=demo DEV_AUTH_PASSWORD=pw uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`.

## Part 2 (messages stream; depends on T3, PR #14)
- `POST /api/conversations/{id}/messages`: `async def`; order of checks: 401 → body 400 (pydantic) → conversation ownership 404 →
  no agent 503 `llm_unavailable` → `agent.stream_turn` (`NotFound` 404, `ValueError` 400: empty / >2000 chars) → only then
  `StreamingResponse(encode_stream(...))`. So every HTTP error is returned before the SSE headers.
- Startup (`api.install`): builds `OpenAIChatClient(LLMConfig.from_env())`; `LLMNotConfigured` → no agent, only this route answers 503, the rest
  works. The `Agent` is built from the same `Airline` as the booking route (same `ConversationLocks`; tested).
  Shutdown: `agent.wait_idle()` then `llm.aclose()`. `create_app(..., llm=, agent_kwargs=)` lets tests inject `FakeLLM`.
- Fixed: lone surrogates in `/api/auth/dev` credentials (`encode("utf-8","surrogatepass")`) → 401, not 500.
- `.env.example`: added `LLM_REASONING_EFFORT`, `OCEAN_ENV_FILE`, `SESSION_SECRET`, `NONCE_SECRET`.
- Tests (+7 in `test_http_api.py`, `FakeLLM`): event order and shapes, card not bookable before `done`, click on the `done` card books,
  cross-user / unknown 404 without calling the model, 401, bad bodies 400, no key → 503 with other routes working, LLM failure → SSE `error`,
  key in env builds the client. Tests clear `LLM_API_KEY`/`DEEPSEEK_API_KEY` and point `OCEAN_ENV_FILE` at nothing, so a developer's `.env` is never used.
- Full suite 427 passed.
