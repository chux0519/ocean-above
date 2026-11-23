# Acceptance — 04-web (2026-11-23)

- **Source:** `main@6659d78` — C1 contract v1.1, B1 #21, F1 #22, D1 #23, DESIGN.md §2–6. Deployed to the homelab demo.
- **Tests:** frontend 62 vitest; backend 600; `tool/test_diff.sh` no new failures; lint ok; `pfstack check`: all set
  (no placeholders left in DESIGN.md).
- **Impeccable** (reports in `D1/`): critique 29/40, audit 16/20, P0–P1 cleared; `detect` on `frontend/src` and on the
  running page: no findings.
- **Screens:** 42 mock screenshots (desktop 1440×900, mobile 390×844 and 360, light + dark) in `D1/screens/`; real
  demo-site run (DeepSeek) in `e2e/`: multi-leg confirm card + no-service card (Austin → Copenhagen), light and dark.
- **Bundle:** streamdown replaced by our minimal renderer: JS 353 → 206 KB gzip; lucide +2 KB gzip.
- **Open (P2, to 05):**
  - after switching the emulated system theme while a page is open, some elements kept the dark tokens until reload
    (seen once in CDP emulation; verify with a real OS switch);
  - a conversation whose first assistant reply came from a booking shows only "Ticket booked." in the list (title fallback);
  - streaming replies cannot be cancelled (needs a backend abort);
  - web build depends on reaching Google Fonts (offline/China builds need a proxy) — decide self-hosting vs Google.
