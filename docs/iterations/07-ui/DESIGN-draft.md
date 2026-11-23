# Draft: DESIGN.md §2–6 rewrite (07-ui, issue #39)

The coordinator merges this into DESIGN.md. It replaces §2–6. §1 Character still holds, with one change: "blue means
act here" is gone (see §2). The §7 entry to add:

> 2026-11-24: 07-ui soft minimalism accepted (user confirmed D1–D7 of
> `docs/iterations/07-ui/proposal/proposal.md`): Geist; ink pill as the primary action; full-height sidebar + phone
> drawer; low-contrast borders + soft shadow with a stronger focus state; vertical route line; 24 px fade above the
> composer; amber border on the confirm card.

---

## 2. Colour
Direction: **soft minimalism** (issue #39, user decision 2026-11-24). The design is a calm, ChatGPT-like
conversation. The itinerary card is the only place with structure and emphasis. Source:
`docs/iterations/07-ui/proposal/proposal.md`.

Strategy: restrained neutrals. Light is warm neutral and dark is charcoal/zinc. The primary action is ink-filled.
One muted ocean-blue accent is used for the route line, links, the focus ring and selection. Status colours always
come with text and, where useful, an icon. Ratios use the WCAG 2.x formula.

### Light

| Token | Value | Use | Contrast |
|---|---|---|---|
| `ground` | `#FAF9F6` | page | — |
| `surface` | `#FFFFFF` | cards, composer, menus | — |
| `sidebar` | `#F4F3EF` | sidebar, drawer | — |
| `sunken` | `#F0EEEA` | user bubble, calculation panel, neutral badge, skeleton | — |
| `hover` / `active` | ink 5 % / 8 % | row and ghost hover; current nav row | — |
| `ink` | `#1C1A17` | text | 17.4 surface, 15.0 sunken |
| `ink-2` | `#5E5A54` | secondary text | 6.9 surface, 5.9 sunken |
| `ink-3` | `#6F6A63` | placeholder, meta, "Transfer", "Flight" | 5.4 surface, 4.6 sunken |
| `hairline` | `#EBE8E3` | dividers inside cards | decorative |
| `border` | `#E2DFD9` | card, composer, outline button | decorative |
| `field` | `#8F8A82` | form-field outline; composer border on focus | 3.4 |
| `primary` / `primary-ink` | `#1C1A17` / `#FFFFFF` | Confirm booking, Send, Go to Chat | 17.4 |
| `accent` | `#1F5F99` | route line (35 %), transfer nodes, links, focus ring | 6.7 |
| `ok` / `ok-bg` | `#1E7340` / `#EAF5ED` | booked, ticket | 5.9 / 5.2 |
| `warn` / `warn-bg` | `#8A5300` / `#FCF1DE` | awaiting confirmation, no service, which one?, notices, similar names | 6.3 / 5.7 |
| `danger` / `danger-bg` | `#B3261E` / `#FCEDEB` | expired, out of date, errors | 6.5 / 5.7 |

### Dark (a re-mix, not an inversion)

| Token | Value | Contrast |
|---|---|---|
| `ground` | `#19191B` | — |
| `surface` | `#222225` | — |
| `sidebar` | `#131315` | — |
| `sunken` | `#2A2A2E` | — |
| `hover` / `active` | white 5 % / 8 % | — |
| `ink` | `#ECECEE` | 13.5 surface |
| `ink-2` | `#A6A6AE` | 6.6 surface, 5.9 sunken |
| `ink-3` | `#96969F` | 5.4 surface, 4.9 sunken |
| `hairline` / `border` | `#2E2E32` / `#34343A` | decorative |
| `field` | `#6E6E77` | 3.1 |
| `primary` / `primary-ink` | `#ECECEE` / `#18181B` | 15.0 (light pill on dark) |
| `accent` | `#7FB3E6` | 7.2 |
| `ok` / `ok-bg` | `#6CCB8B` / `#17291D` | 8.0 / 7.7 |
| `warn` / `warn-bg` | `#E8B25A` / `#2D2414` | 8.3 / 8.0 |
| `danger` / `danger-bg` | `#F28B82` / `#331B19` | 6.6 / 6.7 |

Each colour has one meaning:
- **Amber:** your attention or action is needed.
- **Green:** booked.
- **Red:** cannot proceed.
- **Ink pill:** the one primary action on screen.
- **Blue:** route and links. Blue is never a status.

The theme is chosen as before: it follows the system unless `<html data-theme>` is set, and the stored choice is
applied by the inline boot script, so there is no flash. The System / Light / Dark switch is a segmented control in
the account menu on every page, and in the header on login and the legal pages.

## 3. Type, spacing, shape
- **Family:** Geist (variable, OFL). It is loaded with `next/font/local` from `frontend/src/fonts/`, so builds are
  offline; the licence file is next to it. Figures use `tnum` (`.figures`). One family carries everything.
- **Scale:**

  | Step | px / line height | Use |
  |---|---|---|
  | xs | 12 / 16 | badges, meta, "Transfer", "Flight", timestamps |
  | sm | 14 / 20 | secondary text, buttons, sidebar rows, leg distances |
  | base | 15 / 24 | body default, card text |
  | md | 16 / 26 | chat messages, composer, inputs (no iOS zoom) |
  | lg | 17 / 24 | card titles |
  | xl | 24 / 32 | total price, page titles |
  | 2xl | 28 / 36 | empty-chat headline, legal titles |

- **Weights and case:** 400 body; 500 labels, nav and buttons; 600 titles and the price. No 700. No uppercase labels.
- **Spacing:**
  - 4-pt scale (4 / 8 / 12 / 16 / 20 / 24 / 32 / 40);
  - 32 px between messages;
  - card padding 20 px (16 px on phones);
  - page top padding 40 px from `sm` up.
- **Touch targets:** ≥ 44×44 everywhere (`min-h-11 min-w-11`).
- **Radii (by role):**

  | Radius | Used for |
  |---|---|
  | full | buttons, badges, send, theme switch, the read-only pill |
  | 26 px | composer |
  | 20 px | user bubble |
  | 16 px | cards, menus, history list, empty panel |
  | 12 px | alerts, calculation panel, login fields, candidate rows |
  | 8 px | sidebar rows, ghost buttons |
  | 6 px | inline code |

- **Depth:**
  - `shadow-card`: a very soft 1–2 px shadow in light, none in dark, where the border carries it;
  - `shadow-raised`: the composer, the confirmable card, the login card and the limit notice;
  - `shadow-overlay`: the account menu and the drawer.

  No nested panels.
- **Lines:**
  - 1 px `border` around cards;
  - 1 px `hairline` above the totals;
  - the route line is 2 px of accent at 35 % (grey `border` on dead quotes);
  - the confirmable card's border is amber at 40 %.
- **Layout:**
  - the sidebar is 272 px and becomes a 300 px drawer below 768 px;
  - the chat column and composer are at most 768 px;
  - cards are at most 480 px;
  - assistant text has no container;
  - the user bubble is `sunken`.
- **Fade:** a 24 px fade from `ground` to transparent sits above the composer, so text slides under it. It is
  functional, not decoration.

## 4. Motion
- **Durations:** 150 ms for colour, hover and the menu pop (opacity, 4 px rise, 0.98 scale); 200 ms for card enter
  (opacity plus 4 px rise) and the drawer slide. Easing is `cubic-bezier(0.2, 0, 0, 1)`.
- **Reduced motion:** every duration becomes 0, so state changes stay instant and visible.
- **Loops:** the three-dot typing pulse (1.2 s) and the skeleton pulse. The countdown text changes once a second and
  is not animated.
- **Scrolling:**
  - chat follows new content only while the reader is within 96 px of the bottom, and never yanks someone reading
    further up;
  - away from the bottom, a round "Scroll to latest" button appears above the composer;
  - opening a conversation or sending a message jumps to the bottom.

## 5. Components
These are shadcn/ui-style primitives in `frontend/src/components/ui/` (no Radix, no new packages). Icons are
lucide-react named imports (`icons.tsx`).

| Layer | Component | Notes |
|---|---|---|
| ui | `Button` | pill; default (ink primary), outline, ghost (8 px radius), link; ≥ 44 px; 2 px accent focus ring |
| ui | `Card`, `Badge` | 16 px card with border + `shadow-card`; pill badges in tones muted / ok / warn / danger |
| ui | `Input` | 44 px, 12 px radius, `field` outline, 16 px text |
| ui | `Alert`, `Skeleton` | tinted strip with icon (warn / danger); skeletons shaped like cards |
| app | `Shell` | sidebar (brand, Chat / My tickets / History, page slot, account), phone top bar (menu, name, page action), drawer as a modal dialog with the page behind `inert` |
| app | `AccountPanel` | name, email or sign-in method, usage, theme switch, Sign out, Privacy / Terms |
| app | `ConversationList` | New chat, "Recent" rows (title + preview); portalled into the sidebar |
| app | composer | textarea grows to about 8 lines; Enter sends, Shift+Enter adds a line, never while an IME composes; round send button; reminder line |
| app | `Route` (in `route-card.tsx`) | the signature: origin (filled node) → each transfer city (hollow node, "Transfer") → destination; "Flight <distance>" on each segment; Total distance; Total price (24 px) |
| app | `QuoteCardView` | badge ("Quote · not booked" / "Awaiting your confirmation" / "✓ Booked" / "Expired" / "Out of date", `aria-live`), Valid until or countdown, Route, Confirm booking (ink pill, 48 px), booking error strip, Get a new quote, calculation details (collapsed) |
| app | `TicketCardView` | "✓ Ticket t_…" badge, booked time, Route |
| app | `NoServiceCardView` | amber "No service", "{city} has no flights yet", nearest served cities with distances, first one tinted and marked "Nearest" |
| app | `AmbiguousCityCardView` | amber "Which one?"; candidates are buttons sending "I mean X." (plain rows when read-only) |
| app | `UnknownCityCardView`, `NoticeCardView` | quiet card with a search icon and a suggestion; amber notice strip |
| app | `LoadingState`, `EmptyState`, `ErrorState`, `PageHeader` | skeleton cards; hero (chat) or dashed panel with icon and action; red strip with a ghost Retry; title + one line |

## 6. Screens
### Chat
1. **Hierarchy:** the newest card, then the assistant text, then the composer.
2. **Primary action:** Confirm booking, on a confirmable card only. That card is the only raised one, with an amber
   border, and the button spans its width under the price.
3. **Empty state:**
   - "Where would you like to fly?";
   - one sentence promising every leg, the distance and the price before booking;
   - three example prompts as pills that fill the input (they do not send).
4. **Errors:**
   - Booking errors show inside the card, with "Get a new quote" when needed.
   - Errors sent before the message is stored (`conversation_busy`, `server_busy`, and `internal_error` before
     `user_message`) keep the input text and show a red strip with Retry above the composer.
   - Stream and network errors show the same strip.

### My tickets
1. **Hierarchy:** page title and one line, then ticket cards (two columns from `lg`).
2. **Primary action:** none on the page, except "Go to Chat" in the empty state.
3. **Empty state:** a dashed panel with a ticket icon: "You have no tickets yet…", plus a Go to Chat button.
4. **Error:** a red strip with Retry.

### History
1. **Hierarchy:** page title and one line, then a single bordered list (title, time, preview, chevron).
2. **Detail:** a "‹ All conversations" pill link, then the title, then the read-only pill, then the read-only chat
   with no Confirm and no candidate buttons.
3. **Empty, error and not-found:** a dashed panel; a red strip with Retry; the "not found" text.

### Login
A quiet header (name and theme switch), then a raised "Sign in" card: Google, invite code, local account, separated
by "or", with the legal line below and the footer links.

### Privacy, Terms
The same quiet header, a 28 px title, 16/26 body text at most 672 px wide, and the footer.
