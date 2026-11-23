# Acceptance — 07-ui (2026-11-24)

- **Source:** PR #46 (Closes #39) squash-merged; DESIGN.md §2–6 rewritten from `DESIGN-draft.md` (`4b667ed`);
  released to the VPS.
- **Tests:** frontend 89 vitest; build; test_diff no new failures; lint; `pfstack check` all set.
- **Impeccable:** critique 29/40, audit 18/20, P0–P1 = 0; detect: source clean, running page only `overused-font`
  (Geist, the user's choice).
- **Screens:** 161 screenshots at 1440 / 768 / 390 / 360 × light / dark covering every card state and page
  (`screens/`); production check in `e2e/`.
- **Behaviour kept:** every leg, transfer, total distance and price on quote and confirm cards; Confirm only from
  server state; calculation details collapsed by default; `server_busy` / `conversation_busy` / early `internal_error`
  keep the input with Retry; follow-scroll only when at the bottom; last card fully above the composer on phones.
- **Backlog (P2):** stream cancel needs a backend abort; a one-message booking shows both the quote card and the
  confirmation card for the same quote (consider showing only the confirmation card in that turn).
