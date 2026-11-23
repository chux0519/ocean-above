⚠️ DEGRADED: single-context. The coordinator forbade sub-agents for this task, so Assessments A and B ran in one
context, in order: A was written from screenshots and source before reading any detector output, then B ran.

# /impeccable critique: chat, cards, My tickets, history (light and dark)

- **Targets:**
  - `frontend/src/app/chat/page.tsx` and `components/cards/*`;
  - `frontend/src/app/tickets/page.tsx`;
  - `frontend/src/app/history/**`;
  - `frontend/src/app/login/page.tsx`.
- **Running app:** `http://localhost:3114` against the mock API.
- **Evidence:** `screens/`, 42 PNGs:
  - desktop 1440×900 and mobile 390×844, light and dark, each covering login, the chat scenes (quote, confirm,
    booked, no-service plus ambiguous, error), My tickets, History, the history detail, and on mobile the
    conversation drawer and the settings menu;
  - two extra at 360×740.
- **Mode:** Operate.
- **Direction:** A "Timetable" (`design-proposal.md`).

## Design Health Score
| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 3 | Status badge, countdown and typing status are clear. There is no progress feedback while Confirm is pending, apart from the disabled button. |
| 2 | Match system / real world | 4 | Itinerary set like a timetable: legs, "Transfer at X", distance column, totals rule. |
| 3 | User control and freedom | 3 | "Get a new quote" after a refusal. A streaming reply cannot be stopped. |
| 4 | Consistency and standards | 3 | One Button, Card, Badge and Alert vocabulary. Mobile had two identical hamburger icons (fixed). |
| 5 | Error prevention | 3 | Booking needs an explicit click on a card that shows every leg, the distance and the price. Confirm disappears when the quote has expired or is out of date. |
| 6 | Recognition rather than recall | 3 | Example prompts in the empty chat. Conversation titles and previews in the list. |
| 7 | Flexibility and efficiency | 2 | No keyboard shortcut for a new chat or for focusing the composer. No search in history. |
| 8 | Aesthetic and minimalist design | 3 | Calm and dense where it matters. Large empty right side on desktop chat, because cards are at most 448 px wide. |
| 9 | Error recovery | 3 | Each booking error code has its own message and next step. Stream and network errors show Retry. |
| 10 | Help and documentation | 2 | "Calculation details" explains the numbers. There is no explanation of how booking works beyond the empty-state line. |
| **Total** | | **29/40** | **Good** |

## Design Specificity Verdict
- **Assessment A (unanchored):** specific to the product. The itinerary block is something a generic chat UI does not
  have:
  - a service line with a dot per leg and a ring per transfer city;
  - a right-aligned column of tabular figures;
  - a double rule above the totals;
  - a price that is visibly the largest figure.

  The same distance column runs through the no-service card, so "the numbers come from a table" reads across all
  cards. The navy shell and the cool neutral ground are familiar for an airline, and familiar is the right register
  for Operate mode.
- **Assessment B (deterministic):**
  - `detect --json frontend/src`: `[]`, 0 findings, before and after the fixes.
  - URL scans, round 1: `/tickets` had *skipped-heading* (h1 → h3) at 1440 and 390; `/login` had an advisory
    *straggler* (a single word on the last line); `/chat` and `/history` were clean.
  - URL scans, round 2 after the fixes, on `/login`, `/chat`, `/tickets` and `/history/c_01` at 1440×900 and 390×844:
    `[]` everywhere.

  The detector loads pages in the system theme, which is light, so its dark-theme checks rest on the computed
  contrast table in `design-proposal.md` §2b and on the screenshots. No false positives.
- **Overlay:** not injected. The live-server overlay flow was skipped because the run had to stay single-context.

## What's working
1. **The confirm card carries the full contract of req. 7.** Every leg, every transfer city, the total distance and
   the price sit on one card. A 2 px accent border marks it, and the full-width Confirm sits directly under the
   price. This holds at 360 px (`mobile360-dark-chat-confirm.png`).
2. **Status is always text plus colour.** For example "Awaiting your confirmation" (amber), "Out of date" (red) and
   "✓ Ticket t_91b2" (green). Each colour has one meaning in both themes.
3. **The dark theme is a real second palette, not an inversion.** The accent is lightened to `#4BA3DB` and the text
   on the button turns dark (6.8:1). The status tints are re-mixed so that each pair is at least 6.9:1.

## Priority issues
| Pri | Issue | Why it matters | Fix | Status |
|---|---|---|---|---|
| P1 | On mobile, the header settings button and the "Conversations" button were both a hamburger icon | Two identical icons with different jobs; a first-time user opens the wrong one | Gear icon for settings, labelled "Settings", with `aria-expanded` | **Fixed** (`shell.tsx`) |
| P1 | The mobile drawer (`role=dialog`) did not move focus, close on Escape or return focus | Keyboard and screen-reader users were stranded behind a modal | Focus goes to Close on open; Escape closes; focus returns to the opener; `aria-expanded` on the opener | **Fixed**, with a test (`states.test.tsx`, "chat drawer") |
| P2 | Heading structure: the chat page had no h1; cards were h3 under h1 on My tickets | Screen-reader heading navigation skipped a level, and the chat page had no title | Visually hidden h1 "Chat with Ocean Above"; card titles are h2; the shell content is `<main>` | **Fixed** (detector clean) |
| P2 | History timestamps used the browser locale (shown in Chinese) while everything else is English | Mixed languages look broken to reviewers | `toLocaleString("en", { dateStyle: "medium", timeStyle: "short" })`, plus a chevron that marks rows as links | **Fixed** |
| P2 | A streaming reply cannot be stopped; the composer is locked until it ends | Low control during long agent turns | Stop button (needs an API change: abort the SSE request) | **Open**: behaviour change outside D1. Proposed for a later iteration. |

## Persona red flags
- **Casey (distracted mobile user):**
  - the confirm card fits one 390×844 screen with Confirm visible;
  - the countdown "Expires in 9:57" is small (12 px). Acceptable because expiry also flips the card to the
    "Expired" state with "Get a new quote".
- **Sam (accessibility-dependent user):**
  - the drawer focus problem is fixed;
  - the theme switch is a group of three `aria-pressed` buttons (System, Light, Dark), each with an accessible
    name;
  - the leg lines and dots are `aria-hidden`; the legs are an `<ol aria-label="Route legs">`, so the route is read
    out as text.
- **Jordan (first-timer):** the empty chat explains the promise ("you'll see every leg, the distance and the price
  before anything is booked") and offers three example prompts that fill the input without sending.
- **Take-home reviewer (from PRODUCT.md):**
  - every number on the cards is a `*_display` value; nothing is formatted in the frontend except dates and the
    countdown;
  - "Calculation details" exposes the formula, the coordinates and the high-precision values.

## Minor observations
- Desktop chat leaves a wide empty band to the right of the cards. This is acceptable for a chat with a 768 px
  column; widening the cards would weaken the timetable rhythm.
- The theme buttons in the mobile settings menu are icon-only (each has an `aria-label` and a `title`). Text labels
  would be clearer (P3).
- The global reduced-motion rule sets every duration to 0. The typing indicator still conveys state through its text
  ("Looking up routes…"), so no information is lost.

## Questions to consider
- Should the Confirm button restate the price ("Confirm booking · $1,160.71"), so the commitment is visible on the
  control itself?
- Should My tickets group tickets by date as the list grows?

## Run notes
- No snapshot was written to `.impeccable/critique/`: that is outside the file scope of D1. This report is the
  archive.
- Questions skipped: delegated run. The coordinator relays decisions over hc; the open P2 is listed in STATE.md.
