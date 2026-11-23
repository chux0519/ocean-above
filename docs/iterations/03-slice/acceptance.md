# Acceptance — 03-slice (2026-11-23)

- **Source:** `main@d69704d` (T1 contract, T2 #10, T3 #14 + guard fix #17, T4 #16, T5 #13/#15, T6 #8, T7 #9). Deployed to the
  homelab demo (compose project `ocean-above`), public `/api/health` 200.
- **Goal met:** local and demo end-to-end flow (sign in → quote → booking-request card → click → ticket → my tickets →
  history), see `e2e/README.md` and screenshots (desktop + 390×844).
- **Tests:** backend 599 passed (HYPOTHESIS_PROFILE=ci); frontend 19 vitest; `tool/test_diff.sh` no new failures;
  lint ok. Booking invariants B1–B7/Q1–Q3 model-tested with fixed seeds and 12 mutants.
- **Real-model evals** (deepseek-flash, effort high, 27 cases × 3): trajectory 97.5%, ReplyGuard fallback 1.5%,
  0 safety-invariant violations, $0.039 (report: `T4/report.md`). Total real-model spend in 03 ≈ $0.15.
- **Independent reviews (Fable 5.2):** T2 (0 P0/P1), T3 (0 P0, 4 P1 fixed); passive "booked" claims found by the evals
  and fixed in #17.
- **Process notes:** Fable hit persistent 529s → implementers moved to Opus 5.5 (Fable for reviews); Docker builds on the
  homelab need the proxy env.
- **Carried to 04:** UI polish (Impeccable, shadcn review), streamdown size, tickets loading state, mobile badge wrap,
  "Valid until" on booked cards, `similar_names` on cards, design §§3–6 of DESIGN.md.
- **Carried to 05:** Google login + multi-user, invite login, limits/budget, OAuth production; dev login off.
