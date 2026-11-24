# 09-stop — Stop a running agent turn (#48)

User request (2026-11-24): roadmap item 2, "#48 — Backend Agent cancellation and Stop UI", started after #49 was accepted
(docs/iterations/08-runtime/acceptance.md). Order and reasons: docs/roadmap.md.

## Take-home check (2026-11-24)
| Requirement | Effect | Result |
|---|---|---|
| 7 confirm full route, distance, price before booking | Stop never books and never cancels a booking; a stopped turn mints no new booking authority (cards from it are not bookable) | consistent |
| 9 / 10 tickets, all conversations | a stopped turn is stored with its user message, validated partial text and a `cancelled` status; history reloads it | consistent |
| Numbers traceable | partial text kept only if it already passed ReplyGuard | consistent |
No conflict.

## Decisions (coordinator; the issue leaves them open)
1. **Cards of a stopped turn are not bookable.** Quote cards whose tool finished before Stop are kept and shown
   (information, provenance) but stored as not presented → no nonce, no Confirm button; the user asks again to book.
   This settles P2-7 from the 08 review: "a stopped turn cannot mint fresh booking authority".
2. **A tool interrupted by Stop:** its thread runs to the end (it cannot be killed) and its row is recorded as
   `cancelled` in `tool_calls`; its result is not shown and not given to the model.
3. **Stop vs disconnect:** only the explicit cancel endpoint stops a run; closing the SSE stream keeps today's behaviour
   (the run completes and is reloadable).
4. **Daily message slot:** refunded only if Stop lands before the user message is stored (same rule as today).
5. MVP = immediate cooperative Stop; `after_turn` documented as a future mode.

## Task
| # | Task | Owns | Model |
|---|---|---|---|
| S1 | Backend: app-owned `run_id` + lifecycle (`running → cancelling → cancelled / completed / failed`), in-process run registry (single worker; doc why not multi-worker safe), `POST /api/conversations/{id}/runs/{run_id}/cancel` (auth, ownership, idempotent, an old run_id never cancels a newer run, finished / unknown semantics), built on `Agent.cancel` from 08; SSE: `run_id` on the first event (additive), terminal `cancelled` event; persisted status distinguishes cancelled from completed; decisions 1–4; contract v1.6 + fixtures; invariants (new rules in state_invariants.md first, then model-based / named race tests for every boundary in the issue). Frontend: Stop button (square icon, ≥44 px) in the composer while submitted / streaming → cancel endpoint + abort fetch; "Stopped" state distinct from network errors; authoritative reload; Send restored; no stale updates after switching conversations; vitest. Desktop + mobile screenshots (ego-browser) of the Stop states | `backend/app/agent/**`, `backend/app/api/**`, `backend/app/store/**` (status column / migration v4 only), `backend/app/airline/service.py` (only `post_assistant_message(present=, status=)` and `Message.status` in the view; approved 2026-11-24), `frontend/**`, `docs/api/contract.md`, `contract/**`, `docs/design/state_invariants.md`, tests, `docs/iterations/09-stop/**` | Opus 5.5 |
| R | Fable read-only race review of S1 (cancellation races, locks, slots, B1–B7) | — | Fable 5.2 |

Optional metrics from the issue (`stop_requested`, stop latency, drain time) are recorded as log fields now and become
spans in #35.

## Acceptance (tier A)
- Named regression tests for every race in the issue (before the user message is stored, queued for a slot, during
  model streaming, between model and tool, during sync tool / DB work, while persisting, after completion, repeated
  Stop, two tabs, disconnect without Stop, cross-user, wrong run_id, Stop/finish race → exactly one terminal outcome).
- Fake infinite stream + Stop → upstream closed, no further tokens or tools; lock and slot released; next message works.
- `tool/test_diff.sh` no new failures; lint; B1–B7 model tests; contract fixtures; vitest; evals fake 31/31 and one
  real-model run (N=1) with 0 safety violations.
- ego-browser 1440 and 390: Stop available while waiting for the first token; stopped partial reply visibly marked;
  next message works; no network-error toast.
- Fable review: no open P0/P1. Released to the VPS; production Stop check. README, contract, design.md §4.10 / §6,
  invariants, prompt history updated.
