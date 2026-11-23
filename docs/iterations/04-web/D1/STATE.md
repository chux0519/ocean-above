# D1: STATE (stages 2–3: implementation and review)

The source tested is branch `it04-design`, at the commit that adds this file.

## Done
- **Direction A "Timetable"** implemented with Tailwind theme tokens backed by CSS variables, in two themes, light and
  dark (`frontend/src/app/globals.css`). The decisions are recorded in `design-proposal.md` §0, and the dark palette
  with its contrast ratios in §2b.
- **Font:** Public Sans through `next/font/google`, self-hosted by Next at build time. The woff2 files from stage 1
  were removed.
- **Icons:** `lucide-react`, named imports only, wrapped once in `components/icons.tsx`.
- **Theme:**
  - the default follows the system;
  - `ThemeToggle` offers System / Light / Dark through `aria-pressed` buttons and stores the choice in
    `localStorage["oa-theme"]`;
  - an inline `<head>` script (`lib/theme.ts` `THEME_BOOT`) applies the stored choice before first paint;
  - the switch is in the header on desktop, in the settings menu (gear icon) on phones, and on login.
- **Primitives:** `ui/button`, `ui/card` (Card and Badge), `ui/input`, `ui/alert`, `ui/skeleton`. All app screens
  use them: chat, every card type, My tickets, History, the history detail, login.
- **Accessibility fixes from the review:**
  - the drawer moves focus, closes on Escape and returns focus;
  - a `<main>` landmark and an h1 on chat;
  - card titles are h2;
  - the focus ring on the navy header is white;
  - dates are in English.
- **Reports:**
  - `critique.md`: 29/40 (Good). Both P1s fixed; one P2 open, the stream stop control, which is outside D1.
  - `audit.md`: 16/20 (Good). P0 0, P1 2 (both fixed), P2 3 (2 fixed, 1 open), P3 3.

  Both ran single-context because the coordinator forbade sub-agents, and say so in their first lines.
- **`impeccable detect`:**
  - `frontend/src` → `[]`;
  - running pages `/login`, `/chat`, `/tickets`, `/history/c_01` at 1440×900 and 390×844 → `[]` after the fixes.
    Round 1 had a skipped heading and a straggler; both are fixed.
- **Screenshots** (`screens/`, 42 files): desktop 1440×900 and mobile 390×844, light and dark, covering:
  - login;
  - chat: confirm, quote, booked, no-service plus ambiguous, and the error "out of date" with "Get a new quote";
  - My tickets, History, the history detail;
  - on mobile, the drawer and the settings menu.

  Two extra shots at 360×740 are in the system theme, which was dark in that browser. `scrollWidth == clientWidth`
  on every one; all controls are at least 44×44 at 360. The round "N" button is the Next dev indicator; the purple
  widget at the right edge is a browser extension.

## Sizes (production build, all `.next/static/chunks/*.js`, gzip -9)
| Build | Bytes | Difference |
|---|---|---|
| F1 baseline (own Markdown renderer) | 206,418 | — |
| This branch, icons hand-written as SVG | 213,004 | baseline for the icon comparison |
| This branch, lucide-react, same 14 icons | 214,992 | **icons: +1,988 B** |
| Final (lucide plus the review fixes) | 215,424 | +9.0 KB over F1 for the whole D1 (theme, primitives, icons) |

## Verify
- `cd frontend && npm run lint && npm test && npm run build`: tsc is clean, 62 vitest tests pass and the build is
  OK.
  - New test files: `theme.test.tsx` (default follows the system; the stored choice is applied and cleared; the
    boot script applies a stored choice before React and ignores junk values).
  - New in `states.test.tsx`: the drawer focus and Escape case, and example prompts filling the input without
    sending.
- `bash tool/test_diff.sh`: no new failures. `python3 tool/review_lint.py`: ok.
- To see it with the mock:
  1. `MOCK_PORT=8014 node mock/server.mjs`, or `MOCK_OPEN=1 …` so detect can load pages without signing in.
  2. `API_ORIGIN=http://127.0.0.1:8014 PORT=3114 npm run dev`.
  3. Open http://localhost:3114. Use `localhost`, not `127.0.0.1`: Next dev blocks the latter.
  4. `POST /__mock {"confirm":"multileg"}` shows the multi-leg confirm card.

## Open / notes for the coordinator
- **Deploy:** `next/font/google` downloads the font during `next build`, so the Docker build stage needs outbound
  HTTPS to fonts.googleapis.com and fonts.gstatic.com. Locally I built with `HTTPS_PROXY`. If the homelab build has no
  internet, pass a proxy as a build arg, or fall back to `next/font/local`.
- **Open P2 (not D1):** there is no way to stop a streaming reply; it needs an abort path in the API.
- **P3s are listed in `audit.md`:**
  - theme buttons in the mobile menu are icon-only, with accessible names;
  - the shell overlay tints are not tokenised.
- **DESIGN.md §2–6** can be filled from `design-proposal.md` §2–6, plus §2b and §0. DESIGN.md is not edited by me.
