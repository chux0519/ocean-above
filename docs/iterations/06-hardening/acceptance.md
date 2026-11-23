# Acceptance — 06-hardening P0/P1 (2026-11-24)

- **Merged:** #32 budgets (PR #40), #33 concurrency/timeouts (PR #42), #34 eval regressions (PR #44), #43 runtime review
  fixes (PR #45). P2 (#35, OTel/Phoenix) stays queued; parent #31 stays open for it.
- **Tests:** backend 798 passed (HYPOTHESIS_PROFILE=ci); test_diff no new failures; lint ok. Every review finding has a
  named regression test; mutation checks show refused tool calls and slot accounting are really tested.
- **Independent review (Fable):** no booking or confirmation bypass; 2 P1 (booking clicks starving the thread pool;
  failed attempts over-charged against the token cap) + 9 P2 — all fixed in #43.
- **Real model (deepseek-flash, high):** suite 97.5% → 100% (93 runs); book-in-one-message 50% → 100% (N=10);
  nearest-city follow-up 100%; $0.147. Turn token soft cap default 20k → 60k from measured long-conversation turns.
- **Docs:** design §4.6/4.8/4.9, invariants C1–C4, contract v1.5, `.env.example`.
- **Released** to the production VPS at `2f6f13e` (see docs/deploy.md).
