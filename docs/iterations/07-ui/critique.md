⚠️ DEGRADED: single-context (implementers may not launch other agents, AGENTS.md "Roles"; Assessment A was finished
and written down before the detector output was read).

# Impeccable critique — 07-ui chat screen and cards, light and dark

- **Target:** `frontend/src/app/chat/page.tsx` and every card in `frontend/src/components/cards/`, running on the
  production build (`next build && next start`) against the mock API.
- **Viewports:** 1440, 768, 390 and 360, both themes. The screenshots are in `../screens/`.
- **Mode:** Operate.
- **Personas:** Jordan (first-timer), Casey (phone, one hand), Sam (screen reader and keyboard), and the PRODUCT.md
  reviewer persona "Riley-the-reviewer": an engineer who checks every number and the confirmation gate.

## Design health score (round 1, before fixes → after fixes)

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 3 | Clear status badges, a countdown and a typing indicator. A booked badge change was silent for screen readers (fixed: `aria-live` on the badge) |
| 2 | Match system / real world | 3 | Route line reads as a journey. Leg distances floated without a label, so "1,362.1 km" between two cities was ambiguous (fixed: "Flight 1,362.1 km") |
| 3 | User control and freedom | 2 | A streaming reply cannot be stopped (needs a backend cancel, carried over from D1). Errors before the reply now keep the text and offer Retry |
| 4 | Consistency and standards | 3 | One radius and depth system everywhere. The back link in history used a rotated chevron that did not rotate (fixed: a real left chevron) |
| 5 | Error prevention | 4 | Confirm only on bookable quotes, one-time nonce, expiry and out-of-date shown on the card, dead routes greyed out |
| 6 | Recognition rather than recall | 3 | Ambiguous-city candidates had to be typed again (fixed: they are buttons that send "I mean X." in a live chat) |
| 7 | Flexibility and efficiency | 3 | Enter sends, Shift+Enter adds a line, an IME composition never sends, example prompts. No other shortcuts |
| 8 | Aesthetic and minimalist design | 3 | Calm and content-first. The leg rows are tall on phones (five stops ≈ 300 px) |
| 9 | Error recovery | 3 | Every error says what to do next (Retry, Get a new quote, My tickets) |
| 10 | Help and documentation | 2 | The rule "Nothing is booked until you press Confirm booking" is under the composer. There is no other help |
| **Total** | | **29/40** | **Good** |

## Design specificity verdict
**LLM assessment.** The shell (sidebar, floating composer, prose replies) is intentionally the familiar AI-chat
pattern, as #39 asks. The product's own character lives in the itinerary:
- the vertical route line with hollow transfer nodes and per-leg "Flight" distances;
- the single ink "Confirm booking" pill under the total price;
- the amber-bordered card that is the only raised object while it waits;
- the grey route line on dead quotes;
- the reminder line under the composer.

An unrelated chat product could not reuse the cards unchanged. The shell is deliberately generic. Missed opportunity:
the ticket card is visually the quote card with a green badge. A booked ticket could feel more like a document (for
example the ticket id set larger) without inventing data.

**Deterministic scan.**
- `impeccable detect --json frontend/src` → `[]` (all `.tsx` under `src`).
- The running production pages, scanned with `detect --json <url>` at 1440×900 and 390×844:

  | Path | Findings |
  |---|---|
  | `/chat`, `/tickets`, `/history/c_01` | `overused-font` (Geist) |
  | `/history`, `/login` | none |
  | `/privacy` | round 1: `line-length` (~89 chars); 390: two `straggler` advisories |

  - **`overused-font`** is a false positive by intent: the user chose Geist (decision D1, 2026-11-24), and the brief
    wins.
  - **`line-length`** on `/privacy` is fixed: the article is now `max-w-[65ch]`, and the rescan is clean at 1440.
  - **`straggler`** stays as an advisory: `text-wrap: pretty` is already set, and the legal copy is the
    coordinator's.
  - **`/login`** was scanned with only the invite form (mock `{"login":"invite"}`), because the detector's headless
    browser timed out loading Google's script.

**Visual overlays.** Not used. The live overlay needs a second browser session, and only one ego-browser session is
allowed at a time.

## What's working
1. **The confirmable card is unmistakable.** It is the only raised element, it has an amber border and badge, a
   countdown, and one full-width ink pill directly under the 24 px price. A quote says "Quote · not booked", a ticket
   says "✓ Ticket t_…". Nothing merely quoted looks booked.
2. **Facts stay scannable.** Every leg, every transfer city, the total distance and the price are visible without
   expanding anything. The Decimal provenance is collapsed and quiet.
3. **Two themes from one system.** Dark is a re-mix, not an inversion: the primary pill inverts, there is no shadow,
   borders carry the depth, and every text and status colour is ≥ 4.5:1.

## Priority issues (found in round 1)
| Sev | Issue | Fix | Status |
|---|---|---|---|
| P2 | Leg distance rows had no label | "Flight" prefix (`ink-3`, aria-hidden; the screen-reader text already says "Guangzhou → Frankfurt") | fixed |
| P2 | Ambiguous candidates were not actionable | buttons sending `I mean <city>.` (disabled while streaming, plain rows when read-only) | fixed + test |
| P2 | Disclosure chevron did not rotate; history back link showed a right chevron | inline transform; `ChevronLeft` icon | fixed |
| P2 | No way to stop a streaming reply | needs a backend cancel endpoint, so it stays out of this frontend-only issue | open (carried over from D1) |
| P3 | Ticket card looks like a quote with a green badge | could be distinguished more in a later polish pass | open |

There is no P0 or P1.

## Persona red flags
- **Jordan:** the first screen says what to do and gives three tappable examples. After a quote, the assistant text
  says to press Confirm booking, and the button is the only primary action. Nothing blocking.
- **Casey (390 / 360):**
  - Confirm sits in the lower half.
  - The composer is in the normal flow, so the last card always scrolls fully above it (checked at 360×740 and
    390×844).
  - New chat is in the top bar.
  - Remaining red flag: five-stop routes are tall, so the price can sit below the fold on 360×740 until you scroll.
- **Sam:**
  - every leg has screen-reader text;
  - the status badge is `aria-live`;
  - the drawer is a modal dialog, and the page behind it is `inert` while open;
  - the account menu returns focus to its button on Escape;
  - all targets are ≥ 44 px.

  The countdown text is not announced, on purpose (once a second would be noise).
- **Reviewer:** every number on a card is a `*_display` field. The trace has the high-precision values, the formula
  and the network fingerprint, one click away.

## Minor observations
- On phones the calculation panel wraps long Decimal strings with `break-all`. This is acceptable because it is
  collapsed by default.
- The theme switch is a 3 × 44 px segmented control in the account menu. It is larger than typical but meets the
  touch rule.

Questions skipped: delegated run. The coordinator owns the follow-up decisions.
