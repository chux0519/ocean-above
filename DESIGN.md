# DESIGN — ocean-above

Single source of truth for visual and interaction rules. Only the coordinator edits this file; user decisions
are written here with their date.

## 1. Character
A calm, trustworthy airline booking assistant: a chat where the user types what they want, and the app
answers with precise, checkable facts — every leg, transfer city, distance and price laid out as a clear
itinerary card before anything is booked. Booked tickets and past conversations are always one click away.
It refuses: decorative gradients and illustrations, numbers that only appear in free text (prices and
distances always also appear in a structured card), a "book" action that is not an explicit confirmation,
and colour that means two things.

## 2. Colour
Direction: **A "Timetable"** (user decision 2026-11-23): a quote reads like a printed airline timetable entry — the
itinerary block (service line, one row per leg with right-aligned tabular distances, "Transfer at X" rows, double
rule above the totals, price as the largest figure) is the signature. Source and alternatives:
`docs/iterations/04-web/D1/design-proposal.md`.

Strategy: restrained, plus a committed shell. Neutral surfaces with a cool tint, one accent, and status colours
that always come with text or an icon. Contrast ratios below were computed with the WCAG formula.

| Token | Value | Use | Contrast |
|---|---|---|---|
| `ground` | `#F3F5F7` | page | — |
| `surface` | `#FFFFFF` | cards, composer | — |
| `rail` | `#E9EDF1` | conversation list, skeletons | — |
| `ink` | `#102030` | text, strong rules | 16.5:1 on surface, 15.1:1 on ground |
| `ink-2` | `#4A5866` | secondary text | 7.3:1 on surface, 6.7:1 on ground, 6.2:1 on rail |
| `hairline` | `#CBD3DB` | 1 px dividers (decorative) | 1.5:1 (not used for meaning) |
| `field` | `#8794A1` | input borders | 3.1:1 (non-text ≥ 3:1) |
| placeholder | `#5E6B78` | input placeholder | 5.5:1 |
| `shell` | `#0E2F4A` | header band | white 13.8:1, inactive tab `#B7C9D9` 8.1:1 |
| `accent` | `#0B5D8F` | primary button, focus ring, links, service line, selection | white on accent 7.1:1 |
| `accent-tint` | `#DDE8F2` | user message, text selection | ink on it 13.3:1 |
| `ok` / `ok-bg` | `#1A6B34` / `#E6F4EA` | Booked, ticket | 5.8:1 |
| `warn` / `warn-bg` | `#7A4A00` / `#FFF1D6` | needs your attention: awaiting confirmation, no service, similar names, notice | 6.7:1 |
| `danger` / `danger-bg` | `#B42318` / `#FDECEA` | expired, out of date, errors | 5.8:1 (6.6:1 on white) |

Each colour has one meaning:
- amber means "your attention or action is needed";
- green means "booked";
- red means "cannot proceed";
- blue means "act here".

The "Hub" badge is neutral (it used to be green).

### Dark palette
The dark palette is a re-mix, not an inversion. Surfaces are deep navy; the accent is lightened, and text on
accent-coloured buttons turns dark. Ratios below were computed with the WCAG 2.x formula.

| Token | Dark value | Contrast |
|---|---|---|
| `ground` | `#0C1721` | — |
| `surface` | `#15232F` | — |
| `rail` | `#101D29` | — |
| `ink` | `#E6EDF3` | 13.5:1 on surface, 15.3:1 on ground |
| `ink-2` | `#A3B3C2` | 7.5:1 on surface, 8.4:1 on ground, 8.0:1 on rail |
| placeholder | `#8A9BAB` | 5.6:1 |
| `hairline` | `#2A3B4A` | decorative |
| `field` | `#5F7486` | 3.3:1 (non-text) |
| `shell` | `#0A2236` | white 16.2:1; inactive tab `#9FB6CA` 7.7:1 |
| `accent` | `#4BA3DB` | 5.8:1 on surface; text on it `#06131D` 6.8:1 |
| `accent-tint` | `#1D3A52` | ink on it 10.0:1 |
| `ok` / `ok-bg` | `#6FD08C` / `#12301E` | 7.6:1 |
| `warn` / `warn-bg` | `#F2B84B` / `#3A2A0A` | 7.7:1 |
| `danger` / `danger-bg` | `#FF8A7A` / `#3D1714` | 6.9:1 (7.0:1 on surface) |

On the navy header the focus ring is white in both themes (13.8:1 and 16.2:1).

**How the theme is chosen:**
- With nothing stored, `:root` uses the light tokens, and `@media (prefers-color-scheme: dark)` switches to dark on
  `:root:not([data-theme="light"])`.
- A stored choice sets `<html data-theme>` from an inline `<head>` script before the body is parsed, so there is no
  flash.
- The switch is `components/theme-toggle.tsx`: three `aria-pressed` buttons. It sits in the header on desktop, and
  in the settings menu (gear icon) and on the login page on phones.

## 3. Type, spacing, shape
- **Family:** Public Sans, variable 100–900, loaded with `next/font/google` (self-hosted at build time, `display: swap`, CSS variable `--font-public-sans`). It has `tnum`, which turns on
  tabular figures for every number (`.figures`). The fallback is the system UI stack. One family carries everything.
- **Scale (rem / px, fixed, ratio about 1.2):**

  | Step | px / line height | Use |
  |---|---|---|
  | xs | 12 / 16 | badges, column heads (uppercase, +0.06em), timestamps |
  | sm | 14 / 20 | card body, legs, list rows |
  | 15 | 15 / 24 | chat messages |
  | base | 16 / 24 | inputs (avoids iOS zoom) |
  | lg | 18 / 24 | card titles (`Guangzhou → Lima`) |
  | xl | 22 / 28 | the price |
  | 2xl | 28 / 36 | page titles |

- **Weights:** 400 body, 500 legs and nav, 600 titles and buttons, 700 price and page titles.
- **Spacing:** 4 / 8 / 12 / 16 / 24, plus 32 for page padding on desktop. Card padding is 16 px (20 px from `sm` up).
  The message gap is 20 px.
- **Touch targets:** ≥ 44×44 (`min-h-11 min-w-11` on every button, link and list row).
- **Radii:**
  - 4 px: badges, inline code;
  - 6 px: buttons, inputs, notices;
  - 8 px: cards;
  - 12 px: the user message (4 px at the tail corner).
- **Lines:**
  - 1 px hairline: dividers and card borders;
  - 1 px ink: the column-head rule;
  - 3 px double ink: the rule above the totals;
  - 2 px accent: the border of a card awaiting confirmation;
  - 3 px: the active tab underline.
- **Shadow:** none on cards. Only the mobile drawer has one (`8px 0 24px rgba(16,32,48,.18)`).
- **Layout:**
  - the chat column is at most 768 px, centred, and cards at most 448 px;
  - assistant text has no bubble (it reads as prose) and user text has a tinted bubble;
  - the desktop conversation rail is 288 px and becomes a drawer below 768 px.

## 4. Motion
- **Durations:** 150 ms for hover and colour, 200 ms for card enter (opacity plus 4 px rise) and the drawer slide.
  Easing is `cubic-bezier(0.2, 0, 0, 1)` (ease-out).
- **Reduced motion:** every duration becomes 0 and no loop runs.
- **Loops:** the only indeterminate indicator is the three-dot "typing" pulse shown while a reply streams (1.2 s);
  the skeleton blocks pulse while loading. Nothing else loops. The countdown changes its text once a second and is
  not animated.

## 5. Components
These are shadcn/ui-style primitives copied into `frontend/src/components/ui/`, with no Radix and no new npm
packages. App components wrap them.

| Layer | Component | Notes |
|---|---|---|
| ui | `Button` | variants: default (accent), outline, ghost, link; all ≥ 44 px; focus ring 2 px accent |
| ui | `Card` | flat sheet, 1 px hairline, 8 px radius |
| ui | `Badge` | tones: muted, ok, warn, danger; always text, optional icon |
| ui | `Input` | 44 px, `field` border, accent focus |
| ui | `Skeleton`, `Alert` | tinted strip plus icon; tones warn and danger |
| icons | `icons.tsx` | lucide-react named imports wrapped once (20 px, 1.75 stroke, `aria-hidden`) |
| app | `ThemeToggle` | System / Light / Dark, `aria-pressed`, 44 px |
| app | `ItineraryBlock` (`Route` in `route-card.tsx`) | the signature: service line, leg rows, transfer rows, double rule, totals |
| app | `QuoteCard` / `BookingRequestCard` | status badge plus countdown; accent border while confirmable; Confirm, error, Get a new quote; calculation details (chevron disclosure) |
| app | `TicketCard`, `TicketList` | green "✓ Ticket t_…" badge, booked time in English |
| app | `NoServiceCard` | amber "No service" badge, headline, nearest served cities in the distance column (first in bold) |
| app | `AmbiguousCityCard` | candidates as ruled rows, "Flies to …" |
| app | `UnknownCityCard`, `NoticeCard` | quiet text card; amber notice strip with icon |
| app | `Message` / `MessageList` | user bubble; assistant prose; typing status |
| app | `ConversationList`, drawer | rail on desktop, drawer with close button on phones |
| app | `Shell` | navy band, tabs, sign-out (icon only on phones) |
| app | `LoadingState`, `EmptyState`, `ErrorState` | skeleton cards; teaching empty state; red strip with Retry |

## 6. Screens
### Chat
1. Hierarchy: the newest card, then the assistant text, then the composer.
2. Primary action: **Confirm booking** on a confirmable card. The card gets a 2 px accent border, and the button
   spans the card's full width directly under the price.
3. Empty state:
   - "Where would you like to fly?";
   - one sentence promising that every leg, the distance and the price appear before booking;
   - three example prompts that fill the input (they do not send).
4. Errors:
   - booking errors appear inside the card (red strip with icon), with "Get a new quote" when needed;
   - stream and network errors appear as a red strip with Retry above the composer.

### My tickets
1. Hierarchy: the page title, a one-line explanation, then ticket cards (2 columns from `lg`).
2. Primary action: none. The page is read-only; booking happens in Chat.
3. Empty state: "You have no tickets yet. Book a flight in Chat and it will appear here."
4. Error: a red strip with Retry.

### History
1. Hierarchy: the page title, then conversation rows (title, preview, time).
2. Primary action: open a conversation. The detail view is the read-only chat with no Confirm.
3. Empty state: "No conversations yet."
4. Error: a red strip with Retry. A missing conversation shows "not found" text.

### Login
1. Hierarchy: the product name, one line of purpose, then the form.
2. Primary action: Sign in.
3. Error: inline text under the form.
4. Navy band with the theme switch, then the "Sign in" card.

## 7. Exceptions (dated)
- 2026-11-23: direction A "Timetable"; Public Sans via `next/font/google`; lucide-react icons (named imports); light + dark
  themes following the system with a System / Light / Dark switch; markdown rendered by our own minimal renderer
  (streamdown removed for bundle size: 353 → 206 KB gzip).
- 2026-11-23: mobile is first-class (≥ 360 px wide, no horizontal scroll); screenshots at desktop + mobile.
- 2026-11-23: numbers are shown rounded (distance 1 decimal km, price to the cent) but always computed and
  stored exact.
- 2026-11-23: UI is reviewed with Impeccable (critique / audit / detect); its findings that conflict with this
  file go to the user as a design-conflicts table.
- <YYYY-MM-DD: decision, why, limits.>
