# State invariants

Booking and confirmation rules are checked by model-based tests: a small reference model plus seeded random event
sequences, every invariant asserted after every event. A new state bug gets a new rule id here first, then a test.
Design context: `docs/design/design.md` §6.

## Event alphabet
`quote(user, conv, origin, dest)`, `stream_done(conv)` (cards presented, nonces issued),
`user_message(conv, text)` (incl. negations and questions), `llm_request_booking(conv, quote)`,
`click_book(user, quote, nonce)`, `click_book_concurrent(user, quote, nonce)`, `replay_nonce(quote, nonce)`,
`clock(+Δ)`, `db_change(kind)` (leg removed / coordinate moved / unrelated row changed / file replaced),
`other_user_click(quote, nonce)`, `disconnect_reconnect(conv)`.

## B. Booking
- **B1 Ownership.** A ticket exists only if its quote belongs to the user who clicked.
- **B2 Presented.** A ticket exists only if its quote reached `presented` (its full card was sent with `done`) before the click.
- **B3 Click only.** Every ticket is caused by exactly one `click_book` with a valid, unused nonce for that quote; no
  other event (in particular `llm_request_booking`, any `user_message`) ever creates a ticket.
- **B4 Validity.** No ticket for a quote older than 15 minutes at click time.
- **B5 Idempotent.** At most one ticket per quote, under replay and concurrent clicks.
- **B6 Fresh.** A ticket's legs, coordinates, distance and price strings equal a fresh recomputation on the
  `locations.db` current at click time, and the route is still the cheapest; otherwise the quote is `stale` and no ticket.
- **B7 Atomic.** Quote status and tickets never disagree: `booked` ⇔ exactly one ticket.

## Q. Quotes
- **Q1 No supersede.** Creating a quote never changes the status of another quote.
- **Q2 Button state.** The button state a client sees (after `done` or reconnect) equals the server's quote state.
- **Q3 Terminal.** `booked`, `expired`, `stale` never change again.

## Process
- Seeds are fixed in CI. A failing seed prints its shrunk sequence and is kept as a named regression case.
- Layers: pure model test over `airline/` → API driven with fakes (clock, LLM, locations.db copy) → UI scenario
  (ego-browser) for the click path.

## U. Usage limits
- **U1 Daily cap.** For every user and UTC day, messages let through ≤ `DAILY_MESSAGES_PER_USER`; counting and the
  reservation happen in one write transaction; a refused message stores nothing. (Model test:
  `test_reservations_match_a_reference_count`.) Known: the site token budget is checked at message start, so turns
  already running may overshoot it by their own cost; a `conversation_busy` message has already used its slot.
- **U2 One account per invite code.** An invite code maps to exactly one account, independent of the order of
  `INVITE_CODES`; the name is a display name only.

## S. Sessions
- **S1 Revocable sessions.** A session is valid only while its epoch equals `users.session_epoch` and its login method
  is still enabled; logout bumps the epoch (all of the user's sessions end).

## C. Concurrency
- **C1 Site-wide slots.** At most `AGENT_MAX_ACTIVE_TURNS` turns hold a slot; a refused turn (`server_busy`) stores
  nothing and refunds its message slot. (Model test: `test_slots_model`.)
- **C2 Drain before release.** A turn releases its conversation lock and slot only after every worker thread it started
  has ended, or after the drain timeout (30 s, logged).
