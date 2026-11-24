# 11-followup — conversation load race, busy-quota semantics, Stop before run ID (#55)

User request (2026-11-24): "很好 看一看 issue 55 需要解决". Runs next to 10-otel (#35); before #47.

## Take-home check (2026-11-24)
| Requirement | Effect | Result |
|---|---|---|
| 10 view all conversations | the displayed messages always belong to the selected conversation, whatever order responses arrive in | consistent (fixes a UI race) |
| 7 / B1–B7 | no change to booking, nonce, routing, pricing | consistent |
No conflict.

## Decisions (coordinator, recommended options from the issue)
1. **Load race (P1):** one view-generation guard for every conversation-state update (`load`, `init`, `select`,
   `newChat`, booking refresh, stream completion), so a response only applies while its conversation is still active.
2. **Quota (P1, product decision):** the daily limit counts **user messages that were stored**. Every refusal that
   stores nothing and starts no LLM or tool work releases its exact reservation: `conversation_busy`, the per-user turn
   cap, `server_busy`, and Stop before storing (already done). This changes the documented policy for
   `conversation_busy` (design §4.9, invariant U1 "a `conversation_busy` message has already used its slot"); U1,
   design and contract are updated.
3. **Stop before the run ID (P2):** minimum fix. When Stop is pressed and no cancel acknowledgement arrives (no run
   ID within the grace time), the UI says the outcome is unknown ("Stop may not have reached the server — the reply
   may still finish; reload to see it"), never "Stopped". This is documented as a known limitation, with a regression
   test for headers delayed past the grace time. Client-generated run IDs are written up as the stronger option but
   not built: the header normally arrives within about a second, and a disconnect already keeps today's recovery.

## Task
| # | Task | Owns | Model |
|---|---|---|---|
| F | Decisions 1–3; deterministic vitest with deferred GETs (B-before-A for rapid selection, init vs selection, booking refresh); HTTP-level pytest for each busy path and a normal turn checking the counter and stored messages; Stop-before-header test; U1 + design §4.9 / contract wording | `frontend/**`, `backend/app/api/**`, `backend/app/agent/loop.py` (refund calls only), `backend/app/store/**` (release query only, if needed), `docs/design/state_invariants.md` (U1), `docs/api/contract.md`, tests, `docs/iterations/11-followup/**` | Opus 5.5 |

PR #56 (#35) also touches `loop.py` and `routes.py`. Whichever merges second merges `origin/main` and resolves.
No Fable review: no booking or locking change. The coordinator reviews; if F touches lock or slot ordering, a
review is added.

## Acceptance (tier A)
- New tests fail on the old code (shown once) and pass; `tool/test_diff.sh` no new failures; lint; vitest; evals fake
  31/31; B1–B7 and concurrency tests unchanged and green.
- ego-browser 1440 + 390: the "outcome unknown" Stop notice; quick switching between two conversations.
- Released; design, contract, invariants, prompt history updated.
