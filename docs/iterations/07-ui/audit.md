⚠️ Single-context run (implementers may not launch other agents, AGENTS.md "Roles").

# Impeccable audit — 07-ui, all pages, light and dark

The source tested is branch `it07-ui`, at the commit that adds this file, as a production build
(`next build && next start`) against `frontend/mock/server.mjs`. Screenshots: `screens/` (161 files, see `STATE.md`).

## Audit health score

| # | Dimension | Score | Key finding |
|---|---|---|---|
| 1 | Accessibility | 4 | All text ≥ 4.5:1 in both themes; status never colour-only; drawer is a modal with an inert background; focus returns on Escape |
| 2 | Performance | 3 | One woff2 (69 KB, `display: swap`). No images. Streaming re-renders only the last markdown block (memo). Composer height reads `scrollHeight` once per keystroke |
| 3 | Theming | 4 | Every colour is a token. Zero hex values in components; the only literals are the two `themeColor` meta values |
| 4 | Responsive | 4 | 1440 / 768 / 390 / 360 × light / dark × 20 states: `scrollWidth == clientWidth` on every shot, and no app target under 44 × 44 (see below) |
| 5 | Implementation integrity | 3 | One coherent system. The detector's only repeat is `overused-font` (Geist, chosen by the user). Ticket and quote cards share one layout on purpose |
| **Total** | | **18/20** | **Excellent** |

## Detector
- **Source:** `impeccable detect --json frontend/src` → `[]`.
- **Running pages (production build, 1440×900 and 390×844):**

  | Path | Findings |
  |---|---|
  | `/chat`, `/tickets`, `/history/c_01` | `overused-font` only |
  | `/history`, `/login` (invite form) | `[]` |
  | `/privacy` | `line-length` (fixed, rescan clean), 2 `straggler` advisories (kept) |

  See `critique.md` for why each was kept or fixed.

## Findings by severity

There are no P0 or P1 findings.

**[P2] Streaming reply cannot be stopped**
- **Location:** chat.
- **Impact:** there is no exit while a long reply streams.
- **Recommendation:** add a backend cancel. That is out of scope for this frontend-only issue, and it is carried over
  from D1.

**[P2] Google button height 40 px**
- **Location:** `/login`.
- **Impact:** below our 44 px rule (it meets WCAG 2.5.8 AA at 24 px). It is drawn by Google Identity Services at
  `size: "large"`, which is GIS's maximum.
- **Recommendation:** keep it. The GIS button is the sanctioned control.

**[P3] Inline legal links**
- **Location:** login paragraph.
- **Impact:** 16 px tall, which falls under the WCAG inline-text exception. The same links are also in the footer at
  44 px.

**[P3] Straggler words**
- **Location:** `/privacy` at 390 px.
- **Impact:** cosmetic. The copy belongs to the coordinator.

Fixed in this round (they were found by the critique and the audit):
- the screen-reader announcement of a booking status change (`aria-live` on the status badge);
- account menu focus return on Escape, with `aria-controls` replacing `aria-haspopup`;
- the page behind the drawer is now `inert`;
- the disclosure chevron rotation;
- the back chevron;
- the legal measure;
- the 44 px Privacy / Terms links in the account menu.

## Reduced motion
- Every duration drops to 0, so state changes stay instant and nothing is lost.
- The typing dots stay visible without the pulse.
- "Scroll to latest" scrolls smoothly only when reduced motion is off.

## Positive findings
- **Separate states:** quoted, presented, booked, expired, stale and error each have their own badge text and tone.
  Dead routes are greyed out, and the confirm button exists only while the server says the quote is bookable.
- **Kept input:** messages that were not sent (`conversation_busy`, `server_busy`, and `internal_error` before
  `user_message`) keep the input and offer Retry.
- **Scrolling:** the chat follows new content only near the bottom. In
  `screens/390-dark-chat-reading-up-while-streaming.png`, `scrollTop` stayed at 200 px while a reply streamed, and
  "Scroll to latest" appeared.
