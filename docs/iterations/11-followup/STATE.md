# 11-followup — STATE (task F, #55)

Branch `it11-followup`. Tested source: commit `22b13b1` (code) + this docs/mock commit. Implementer: Opus 5.5.

## Done
| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | P1 load race: `load()` set state after any `GET /conversations/{id}`, so A resolving after B replaced B's messages; also init and the booking refresh. While B loaded, A's messages stayed on screen under B's selection. | `frontend/src/app/chat/page.tsx`: one view generation (`view`) for every conversation-state write. `enter(id)` bumps it (via `detach`), sets the selection and clears the pane at once; `load(id, gen)` applies data, errors and `loadingConv` only while `gen` is current. `init` (skips if the user opened/started a chat meanwhile), `select`, `newChat` (also checks after `POST`), booking refresh (`refreshAfterBooking`, skipped while a reply streams; that stream's own reload follows) and the post-stream reload (unchanged, `here()`) all use it. Found while testing: a chat created while the first list load was in flight was dropped by `setList(items)` → merged now. | `frontend/tests/load-race.test.tsx` (8, deferred GETs released B-before-A): rapid selection A/B/A/B; stale failing load shows no error; init vs selection; New chat during list load; New chat resolving after the user picked B; New chat while A loads; booking refresh after switching to B; booking refresh when staying (control). |
| 2 | P1 quota: `conversation_busy` (lock timeout) and the per-user cap reserved a slot and never refunded it, while the UI said "not sent". | Decision 2: the limit counts **stored** user messages. `backend/app/agent/loop.py`: both branches set `run.state = "finishing"` (a Stop from then is too late, like `server_busy`), call `not_stored()` (the route's `release_message(user, reservation)`: `DELETE … WHERE id = ? AND user_id = ?`, i.e. exactly that reservation; idempotent), then emit. Reservation order, lock → user cap → slot order, B1–B7, nonce, routing, pricing untouched. | `backend/tests/test_busy_quota.py` (10): HTTP lock timeout, HTTP per-user cap, HTTP `server_busy`, HTTP normal turn — each checks `/api/me` counter, `message_usage` rows (the running turn's row stays) and stored messages; retry after refusal counts once; at limit 2 a refused message does not block the retry; refund callback called exactly once per path (lock/user/slots); Stop during a busy refund answers `finishing` twice, refund once, one terminal event. |
| 3 | P2 Stop before run id: with headers late, Stop aborted after 3 s and the UI announced "Reply stopped." although no cancel was ever sent. | Decision 3 (minimum): `Live.acked` is set when the cancel endpoint answers. Stop with no ack and no terminal event → notice `STOP_UNKNOWN` ("Stop may not have reached the server — the reply may still finish. Reload to see it.") with a Reload button (44 px), same text to the live region; never "Stopped"; input text kept. Also covers a failing cancel request. Client-generated run ids: written up in contract (not built). | `frontend/tests/stop.test.tsx` +3: headers delayed past grace (no cancel, unknown notice, no stopped marker, input kept, Reload shows the reply that finished); run id known but cancel fails; acknowledged Stop is not "unknown". Backend `tests/test_stop.py::test_55_headers_never_reached_the_client…`: the accepted run completes and is stored, slot kept, a late Stop returns `completed` (characterization of the guarantee — passes on old code too, by design). |

## Failing on the old code (shown once)
- Backend, `loop.py` from `099d74a`, `pytest tests/test_busy_quota.py`: **7 failed, 3 passed** (normal turn, `server_busy` HTTP, `server_busy` refund-once already worked). Failures: `assert 2 == 1` (counter, lock and user paths), `assert 3 == 2` (retry), `assert (429 == 200)` (retry at limit 2), `assert [] == ['second']` ×2 (no refund call), `'failed' == 'finishing'` (no refund at all to race with).
- Frontend, `page.tsx` from `099d74a`, `vitest tests/load-race.test.tsx`: **7 failed, 1 passed** (the "user stayed" control).
- Frontend, `page.tsx` from `099d74a`, `vitest tests/stop.test.tsx`: the 2 new unknown-outcome tests fail (`Unable to find [data-testid="stop-unknown"]`); 15 pass.

## Docs changed
- `docs/design/state_invariants.md` U1: counts stored user messages; every refusal before storing/without LLM-or-tool work refunds its own reservation at most once; Stop is too late once refunding; removed "a `conversation_busy` message has already used its slot".
- `docs/design/design.md`: §4.5 lock-wait line, §4.6 table row, §4.9 per-user cap ("额度退还") + new "额度口径（#55）" paragraph, §8.2.
- `docs/api/contract.md` v1.7 (no wire change): `error` row — `conversation_busy` also refunds; Stop section — "Stop before the run id is known" limitation, unknown-outcome wording, stronger option (client run id + cancel tombstone) not built.
- Coordinator still to do: README / design §11 known limitations line if wanted, prompt history export.

## Verification
- `HYPOTHESIS_PROFILE=ci uv run pytest -q` (backend): **913 passed**.
- `npx vitest run` (frontend): **114 passed**; `npx tsc --noEmit` ok.
- `bash tool/test_diff.sh`: no new failures (0 known). `python3 tool/review_lint.py`: ok.
- `python -m evals --mode fake`: 0 failures of 31 runs.
- ego-browser against `next dev` + `mock/server.mjs` (new scenarios `{"message":"headers_late"}`, `{"switch":{"slow":"c_01","ms":2500}}`), shots in `shots/`:
  - `stop-unknown-1440.png`, `stop-unknown-390.png`: notice + Reload under the composer; input kept; scrollWidth = viewport.
  - `switch-1440.png`, `switch-390.png`, `switch-390-nav.png`: Abu Dhabi → Guangzhou (2.5 s) → Abu Dhabi; after the slow load lands, the selected item and the pane are both Abu Dhabi (checked by DOM too).
  - The floating purple widget at the right edge is a browser extension of the local profile, not the app.

## Not done / open
- Client-generated run ids (decision 3: documented only).
- PR #56 (#35) also edits `loop.py`; whichever merges second resolves (`_run` except-branches here).
