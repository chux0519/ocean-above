# 11-followup — acceptance (2026-11-24)

Scope: #55. Plan: [plan.md](plan.md); details and old-code failure evidence: [STATE.md](STATE.md).

- **Load race (P1):** one view generation guards every conversation-state write (init, select, New chat, booking
  refresh, post-stream reload). 8 deterministic vitest cases with deferred GETs (B before A); 7 fail on the old code.
  Also fixed: a chat created while the first list load was in flight was dropped.
- **Busy quota (P1, decision 2):** the daily limit counts stored user messages. `conversation_busy` (lock timeout and
  per-user cap) now refunds exactly its own reservation, at most once, like `server_busy`. 10 HTTP-level tests check
  the `/api/me` counter, `message_usage` rows and stored messages; 7 fail on the old code. U1, design §4.5 / §4.6 /
  §4.9 / §8.2 and contract v1.7 (wording only, no wire change) updated.
- **Stop before the run ID (P2, decision 3):** without a cancel acknowledgement the UI says the outcome is unknown and
  offers Reload; never "Stopped". Client-generated run IDs are documented as the stronger option, not built.
- **Tests (merged `0789110`, verified by the coordinator):** backend 913 passed, vitest 114 passed, lint clean,
  `tool/test_diff.sh` no new failures, evals fake 31/31. No real-model run (no agent-loop behaviour change beyond the
  refund call).
- **Screenshots:** `shots/` — unknown-outcome notice at 1440 and 390, quick switching between two conversations.
- **Released** `0789110` (see docs/deploy.md).
