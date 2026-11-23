# L3 frontend — STATE (2026-11-23)

Branch `it05-web` · issue #26 · tested source: the commit that adds this file (main merged at 8705771, L1 + L2 included).

## Done
- **Login from `/api/config`** (`src/app/login/page.tsx`, `src/components/login-options.tsx`): Google Identity Services
  button (`src/components/google-button.tsx`, loads `https://accounts.google.com/gsi/client` once, ID-token popup flow,
  `POST /api/auth/google {credential}`), invite form (code + name, `maxLength` 40), dev form only when `dev_login`.
  Each form has its own inline error (401, 429 login throttle, 503). Config loading / error + Retry; "sign-in not
  available" when nothing is enabled. GIS: `locale: "en"`; container `color-scheme: light` so the GIS iframe is not
  painted opaque white in dark mode.
- **Account + usage**: header account button (name on desktop, icon on phone) opens one menu: name, email (or the
  sign-in method when there is none), `n of N messages today` (both numbers verbatim from `me.usage`), theme (phone),
  Sign out, Privacy / Terms. `Shell` passes `refreshUser`; chat calls it after every send (and after a 429).
- **429 on send** (`src/components/limit-notice.tsx`, chat page): heading by `scope` (user limit / site budget) + the
  server message (states the reset time). Input kept, no reload (nothing stored), notice clears on the next send.
  `ApiError` now carries `scope` and `resetsAt`.
- **Font**: Public Sans variable woff2 (full glyph set, 42 KB, OFL) in `src/fonts/`, `next/font/local`. `next build`
  passes with all proxies unset; no Google Fonts request at build or run time.
- **COOP**: `Cross-Origin-Opener-Policy: same-origin-allow-popups` on every page via `next.config.ts` `headers()` (so it
  also applies without Caddy). Caddyfile unchanged.
- **/privacy, /terms**: static pages rendering `docs/iterations/05-launch/legal.md` verbatim. The web image is built
  from `frontend/` only, so the text is copied to `src/content/legal.md`; `tests/legal.test.ts` fails if the copy
  differs from the doc (fix: `cp docs/iterations/05-launch/legal.md frontend/src/content/legal.md`). Linked from the
  login card, the login / legal page footer, and the account menu.
- **Mock** (`mock/server.mjs`): `/api/auth/invite` (code `example-invite`), `/api/auth/google` (accepts anything),
  scenarios `{"message":"limit"|"site_limit"}`, `{"login":"google"|"invite"|"dev"}`; `MOCK_GOOGLE_CLIENT_ID` env shows
  the real GIS button on localhost.

## Evidence
- `npm run typecheck` clean; vitest **82 passed** (new: `login.test.tsx` 9, `usage.test.tsx` 4, `rate-limit.test.tsx` 2,
  `legal.test.ts` 4). `bash tool/test_diff.sh`: no new failures (0 known). `python3 tool/review_lint.py`: all ok.
- Screenshots `screenshots/` (ego-browser, 1440×900 and 390×844): `login-{light,dark}-{desktop,mobile}`,
  `limit-{light,dark}-{desktop,mobile}` (mock), `privacy-light-desktop`, `terms-light-mobile`,
  `account-light-{desktop,mobile}`; mobile `scrollWidth` = 390 (no horizontal scroll).
- **Real backend** (main with L1 + L2, `INVITE_CODES=example-invite-1`, `DAILY_MESSAGES_PER_USER=0`, no dev account):
  `real-login-prodconfig-desktop` (Google + invite only), wrong code → "That invite code is not valid." inline;
  right code → /chat; send → 429 notice with the server text, input kept; `real-account-desktop` shows
  "Reviewer A / Invite code / 0 of 0 messages today". The real GIS button rendered with the provided client ID on
  localhost:3100 (popup sign-in itself not automated).

## Not done / open
- `npx impeccable detect` hung (> 200 s, no output, with and without proxy) and was stopped; not run this round.
  No `/impeccable critique|audit` either (not in the issue's scope). Coordinator may want a pass on login + account menu.
- The GIS button is 40 px tall (Google's `large` size, not adjustable); its container is 44 px.
- GIS button width is computed once at render; it does not re-render on a later viewport resize.
- "Footer" in the issue: the authenticated app has no footer (full-height chat), so the legal links live in the
  account menu there; login and legal pages have a footer.

## How to verify
`cd frontend && npm test`; `MOCK_PORT=8077 node mock/server.mjs` + `API_ORIGIN=http://127.0.0.1:8077 PORT=3100 npm run dev`,
then `/login`, `/privacy`, `/terms`, and `curl -X POST localhost:8077/__mock -d '{"message":"limit"}'` before sending a chat message.
