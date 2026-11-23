# 07-ui stage 1 — design proposal (issue #39)

Status: **waiting for the user's confirmation.** The proposal is implemented on branch `it07-ui` (not merged), so the
screenshots below come from the real app (Next dev server + `frontend/mock/server.mjs`, scenario
`{"scene":"proposal"}`), captured with ego-browser. No static mock-ups.

Direction: a calm, ChatGPT-like conversation layout with Linear-like restraint. Light is warm neutral, dark is
charcoal/zinc. Depth is soft and used sparingly, rounding follows the role of each element, and the itinerary card
is the visual centre.

## 1. Screenshots

All are in `screens/`. Desktop is 1440×900 and mobile is 390×844 (rendered at 2×). Each viewport has a light and a
dark set.

| Scene | Desktop | Mobile |
|---|---|---|
| Multi-leg **quote** (not booked), user bubble, assistant prose | `desktop-{light,dark}-chat-quote.png` | `mobile-{light,dark}-chat-quote.png` |
| **Confirm** card (awaiting confirmation, countdown, Confirm booking), floating composer | `desktop-{light,dark}-chat-confirm.png` | `mobile-{light,dark}-chat-confirm.png` |
| Calculation details expanded (collapsed by default everywhere else) | `desktop-{light,dark}-chat-calc-open.png` | — |
| Sidebar as a drawer | — | `mobile-{light,dark}-drawer.png` |
| Composer focused, two lines of text (Shift+Enter) | — | `mobile-{light,dark}-composer-typing.png` |

**Before:** `docs/iterations/04-web/D1/screens/` (e.g. `desktop-light-chat-confirm.png`, `mobile-dark-chat-confirm.png`).

On every shot, `scrollWidth == clientWidth`, so there is no horizontal scroll. The purple widget at the right edge is
a browser extension, not part of the app.

## 2. Colour tokens

Contrast is computed with the WCAG 2.x formula (script output pasted below the tables). Tokens live in
`frontend/src/app/globals.css` as CSS variables and are exposed to Tailwind through `@theme inline`.

### Light — warm neutral

| Token | Value | Use | Contrast |
|---|---|---|---|
| `ground` | `#FAF9F6` | page, chat canvas | — |
| `surface` | `#FFFFFF` | cards, composer, menus | — |
| `sidebar` | `#F4F3EF` | sidebar / drawer | — |
| `sunken` | `#F0EEEA` | user bubble, calculation panel, neutral badge, skeleton | — |
| `ink` | `#1C1A17` | text | 17.4 surface · 16.5 ground · 15.0 sunken |
| `ink-2` | `#5E5A54` | secondary text | 6.9 surface · 6.5 ground · 5.9 sunken |
| `ink-3` | `#6F6A63` | placeholder, meta, timestamps | 5.4 surface · 5.1 ground · 4.8 sidebar · 4.6 sunken |
| `hairline` | `#EBE8E3` | dividers inside cards | decorative |
| `border` | `#E2DFD9` | card and composer outline | 1.3, decorative (see decision D4) |
| `field` | `#8F8A82` | form-field outline where the border is the only cue (login) | 3.4 (non-text ≥ 3) |
| `primary` / `primary-ink` | `#1C1A17` / `#FFFFFF` | the one primary action (Confirm booking, Send) | 17.4 |
| `accent` | `#1F5F99` | route line, links, focus ring, selection | 6.7 surface |
| `ok` / `ok-bg` | `#1E7340` / `#EAF5ED` | **booked** only | 5.9 / 5.2 |
| `warn` / `warn-bg` | `#8A5300` / `#FCF1DE` | **needs you**: awaiting confirmation, no service, similar names | 6.3 / 5.7 |
| `danger` / `danger-bg` | `#B3261E` / `#FCEDEB` | **cannot proceed**: expired, out of date, errors | 6.5 / 5.7 |

### Dark — charcoal / zinc (a re-mix, not an inversion)

| Token | Value | Contrast |
|---|---|---|
| `ground` | `#19191B` | — |
| `surface` | `#222225` | — |
| `sidebar` | `#131315` | — |
| `sunken` | `#2A2A2E` | — |
| `ink` | `#ECECEE` | 13.5 surface · 14.9 ground · 12.1 sunken |
| `ink-2` | `#A6A6AE` | 6.6 surface · 7.3 ground · 5.9 sunken |
| `ink-3` | `#96969F` | 5.4 surface · 6.0 ground · 4.9 sunken |
| `hairline` / `border` | `#2E2E32` / `#34343A` | decorative |
| `field` | `#6E6E77` | 3.1 surface |
| `primary` / `primary-ink` | `#ECECEE` / `#18181B` | 15.0 (the button inverts: light pill on dark) |
| `accent` | `#7FB3E6` | 7.2 surface |
| `ok` / `ok-bg` | `#6CCB8B` / `#17291D` | 8.0 / 7.7 |
| `warn` / `warn-bg` | `#E8B25A` / `#2D2414` | 8.3 / 8.0 |
| `danger` / `danger-bg` | `#F28B82` / `#331B19` | 6.6 / 6.7 |

Each colour keeps one meaning, as before: amber = your action is needed, green = booked, red = cannot proceed. Blue
no longer means "act here". The primary action is now ink-filled (black pill in light, light pill in dark), and blue
is reduced to the route line, links and the focus ring. A status is never carried by colour alone: every badge has
text, and booked badges also have a check icon.

## 3. Shape, depth, type, spacing

**Radii are chosen by role, not applied everywhere.**

| Radius | Used for |
|---|---|
| full (pill) | buttons, badges, the send button, the theme switch |
| 26 px (`3xl`) | composer |
| 20 px (`2xl`) | user bubble |
| 16 px (`xl`) | cards, menus |
| 12 px (`lg`) | inner panels: alerts, calculation details, login fields |
| 8 px (`md`) | sidebar rows, ghost buttons |
| 6 px (`sm`) | inline code |

Assistant text has no container at all.

**Depth:**

| Token | Light | Dark | Used for |
|---|---|---|---|
| `shadow-card` | 1–2 px very soft (≤ 5 % ink) | none; the 1 px border carries it | cards |
| `shadow-raised` | | | composer, the confirm card, notices |
| `shadow-overlay` | | | account menu, drawer |

**No nested panels.** A card holds the route directly: no inner boxes, no table rules, no double rule. The only inset
panel is the opened calculation details.

**Type: Geist** (variable 100–900, OFL), loaded with `next/font/local` from `frontend/src/fonts/`, so the build works
offline. The licence file is `Geist-OFL.txt`, and `fonts/README.md` records the source and sha256. Public Sans is
removed. Figures use `tnum` (`.figures`).

| Step | px / line height | Use |
|---|---|---|
| xs | 12 / 16 | badges, meta, timestamps, "Transfer" tag |
| sm | 14 / 20 | secondary text, buttons, sidebar rows, leg distances |
| base | 15 / 24 | body default, card text |
| md | 16 / 26 | chat messages, composer (16 px avoids iOS zoom) |
| lg | 17 / 24 | card title `Guangzhou → Lima` |
| xl | 24 / 32 | the total price |
| 2xl | 28 / 36 | empty-state headline |

- **Weights:** 400 body, 500 labels and nav, 600 titles and price. No 700, no uppercase labels ("CONVERSATIONS" →
  "Recent").
- **Spacing:** 4-pt scale (4 / 8 / 12 / 16 / 20 / 24 / 32 / 40). 32 px between messages. Cards have 20 px padding
  (16 on phones).
- **Widths:** the chat column and composer are at most 768 px; cards are at most 480 px; the sidebar is 272 px and
  becomes a drawer below 768 px.
- **Motion:** unchanged from 04-web. 150 ms colour, 200 ms enter/drawer, ease-out, and every duration drops to 0 under
  `prefers-reduced-motion`.

## 4. Representative screen: what changed and why

| Area | Before (D1 "Timetable") | Proposal | Why |
|---|---|---|---|
| Frame | navy header band + tabs; grey rail with conversations | one quiet full-height sidebar (brand, Chat / My tickets / History with icons, New chat, Recent, account at the bottom); no header on desktop; phone: a 56 px bar with menu + name | the band was the heaviest object on screen; navigation now sits with the conversations |
| Theme switch, sign-out | in the header (desktop) / gear menu (phone) | account menu (avatar row at the bottom of the sidebar) on all sizes | one place; fewer header controls |
| Messages | 15 px; tinted blue user bubble | 16/26; user bubble is a soft `sunken` pill; assistant is plain prose | content-first, calmer |
| Itinerary | table: column heads, dashed "Transfer at" rows, double rule | vertical **route line**: origin (filled) → each transfer city (hollow node + "Transfer") → destination (filled); each leg's distance on its segment; then Total distance and a 24 px **Total price** | reads as a journey, not a spreadsheet; every leg and every transfer city is still shown |
| Status | "Quote" badge; confirm card = 2 px blue border | "Quote · not booked" (neutral); "Awaiting your confirmation" (amber) + countdown + amber-tinted border + raised shadow; "✓ Booked" / "✓ Ticket …" green | a quote can no longer be read as a ticket; the card that wants action is the only raised one |
| Confirm | blue full-width button | ink pill, 48 px, full width, directly under the price | the single primary action on the screen |
| Calculation | chevron + grey box | still collapsed by default; quieter button; `sunken` panel when open | provenance stays one click away |
| Composer | bordered input + "Send" in a bottom bar | floating 26 px-radius composer with a round send button, auto-growing textarea (to about 8 lines), Enter sends, Shift+Enter adds a new line, **no send while an IME is composing**, safe-area padding; a one-line reminder below: "Nothing is booked until you press Confirm booking on a quote." | the ChatGPT pattern; the reminder states the booking rule once |

Kept, with checks:
- Numbers only from `*_display`. The card shows no counts of its own (no "3 stops"); the subtitle stays
  "Via Frankfurt, Singapore, Amsterdam".
- Screen readers still get one "Guangzhou → Frankfurt" text per leg. The leg list is an `ol` labelled "Route legs".
- Every touch target is at least 44×44: send, nav rows, conversation rows, theme buttons and the menu button.
- Dead quotes (expired or stale) turn the route line grey, so they don't look live.

Other code changes:
- The drawer logic moved from the chat page into `Shell`, so the drawer now works on every page. Chat renders its
  conversation list into the sidebar through a portal.
- The drawer test moved to `tests/usage.test.tsx` (it renders the real `Shell`). The buttons are now named
  "Open menu" / "Close menu".
- Login and the legal pages only lost the navy band, so they don't break with the new tokens. Their full restyle is
  stage 2.
- The mock has a new `{"scene":"proposal"}` scenario.
- `next.config.ts` sets `devIndicators: false`, so dev screenshots are clean.

## 5. Decisions for you to confirm

| # | Decision | Proposal | Alternative |
|---|---|---|---|
| D1 | Font | **Geist**, local file | Inter (also OFL, slightly wider, more neutral) |
| D2 | Primary action colour | **ink pill** (black in light, light in dark); blue only for route line, links and focus | keep the blue accent as the primary button colour |
| D3 | Navigation | **full-height sidebar** with nav + conversations + account; phone drawer | keep a thin top bar with tabs above the chat |
| D4 | Composer and card outlines | low-contrast 1 px border (1.3:1) + soft shadow. The composer is identified by its placeholder, its send button and its position, and on focus it gets a `field` border (3.4:1) + accent ring | a 3:1 outline at rest (less soft) |
| D5 | Itinerary layout | **vertical route line** with distances on the segments | keep a two-column (leg / distance) table inside the new card style |
| D6 | Fade above the composer | a 24 px fade from the page colour to transparent, so text slides under the composer (functional, not decorative) | a hard edge |
| D7 | Confirm-card emphasis | amber-tinted border + raised shadow (amber = needs you) | neutral border + shadow only |

## 6. Verification (source = the commit that adds this file)

| Check | Result |
|---|---|
| `npx tsc --noEmit` | clean |
| `npx vitest run` | 84 passed (11 files) |
| `npx next build` | OK |
| `python3 tool/review_lint.py` | all ok |
| `npx impeccable detect --json src` and `--json http://localhost:3117/chat` | no findings printed (exit 0) |

The full Impeccable critique and audit are planned for stage 3.

Known follow-ups for stage 2, not done here:
- every other card (no-service, ambiguous, unknown, notice, ticket list);
- My tickets and History;
- login and legal;
- `server_busy` (contract v1.4);
- the empty and error states at 360 / 768;
- the stream "stop" control.

Contrast script output (WCAG 2.x):

```
L.ink       #1c1a17 #ffffff:17.36 #faf9f6:16.49 #f4f3ef:15.64 #f0eeea:14.98
L.ink-2     #5e5a54 #ffffff:6.85  #faf9f6:6.50  #f4f3ef:6.17  #f0eeea:5.91
L.ink-3     #6f6a63 #ffffff:5.36  #faf9f6:5.09  #f4f3ef:4.83  #f0eeea:4.63
L.field     #8f8a82 #ffffff:3.43
L.primary   #ffffff on #1c1a17:17.36
L.accent    #1f5f99 #ffffff:6.66  #faf9f6:6.32
L.ok        #1e7340 #ffffff:5.86  #eaf5ed:5.24
L.warn      #8a5300 #ffffff:6.33  #fcf1de:5.66
L.danger    #b3261e #ffffff:6.54  #fcedeb:5.74
D.ink       #ececee #222225:13.45 #19191b:14.88 #131315:15.73 #2a2a2e:12.12
D.ink-2     #a6a6ae #222225:6.56  #19191b:7.26  #131315:7.68  #2a2a2e:5.91
D.ink-3     #96969f #222225:5.41  #19191b:5.99  #131315:6.33  #2a2a2e:4.87
D.field     #6e6e77 #222225:3.14
D.primary   #18181b on #ececee:15.02
D.accent    #7fb3e6 #222225:7.17  #19191b:7.94
D.ok        #6ccb8b #222225:7.97  #17291d:7.70
D.warn      #e8b25a #222225:8.27  #2d2414:7.97
D.danger    #f28b82 #222225:6.64  #331b19:6.70
```
