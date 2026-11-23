# Acceptance — T2 store + airline (2026-11-23)

- Tested source: PR #10 head `434fac9` (squash-merged to `main`). Coordinator reran backend tests with
  `HYPOTHESIS_PROFILE=ci`: 263 passed; lint ok; PR scope = store, airline, the WAL guard in core, tests, T2 docs.
- Independent read-only review (Fable 5.2): no P0/P1; 9 P2, all handled in round 02 (WAL refused by core, surrogate
  nonce → invalid_nonce, 5 s lock wait → `conversation_busy`, unreadable data → `data_unavailable` without consuming
  the nonce, clock read inside the write transaction, ownership on Store conversation methods, 12 mutants each caught
  under its rule id).
- Decisions accepted: HMAC-derived nonce + `quote_cards` table; nonce issued at `done`; `request_booking` same
  conversation only; `similar_names` only in tool data for now. Design §3.2/§4.4/§7 and the contract synced.
- Pending: frontend part of test_diff was not exercised in the worker worktree (no node_modules); runs on `main`.
