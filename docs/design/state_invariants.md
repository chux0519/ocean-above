# State invariants

Booking and confirmation rules are checked by model-based tests: a small reference model plus seeded random event
sequences, every invariant asserted after every event. A new state bug gets a new rule id here first, then a test.
Design context: `docs/design/design.md` §6.

## Event alphabet
`quote(user, conv, origin, dest)`, `stream_done(conv)` (cards presented, nonces issued),
`user_message(conv, text)` (incl. negations and questions), `llm_request_booking(conv, quote)`,
`click_book(user, quote, nonce)`, `click_book_concurrent(user, quote, nonce)`, `replay_nonce(quote, nonce)`,
`clock(+Δ)`, `db_change(kind)` (leg removed / coordinate moved / unrelated row changed / file replaced),
`other_user_click(quote, nonce)`, `disconnect_reconnect(conv)`, `stop_turn(conv)` (R6).

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
- **C3 Clicks hold no worker thread while waiting.** A booking click waits for the conversation lock on the event
  loop; its transaction runs to its end before the lock is released, even when the request is cancelled.
- **C4 Per-user caps.** At most `AGENT_MAX_TURNS_PER_USER` turns and 2 pending booking clicks per user at once.

## R. Stop (user cancellation of a running turn, #48)
Event alphabet for these rules: `send(conv)` (a turn starts, gets `run_id`), `stop(user, conv, run_id)` (the cancel
endpoint), `disconnect(conv)` (the SSE reader goes away), `model_token`, `tool_start`, `tool_end`, `persist`, `finish`.
Booking model (B/Q): `stop_turn(conv)` = the turn's pending cards are stored with a `cancelled` message and are **not**
presented.
- **R1 Explicit only.** A run is cancelled only by `stop` from its owner, naming its own conversation and `run_id`. A
  disconnect, a closed tab or a failed SSE read never cancels (the run completes and is stored). A `run_id` of an
  older run, of another conversation or of another user never cancels anything (404, nothing changes).
- **R2 Nothing new after Stop.** Once a run is cancelling, it starts no LLM request, no retry and no tool call; the
  model's HTTP stream in flight is closed. A worker thread that already started runs to its end (it cannot be killed).
- **R3 Drain before release.** C2 holds for a stopped run: the conversation lock, the turn slot and the per-user count
  are released only after its worker threads ended (or after the drain timeout, logged), and before its terminal event.
- **R4 One terminal outcome.** Every run ends with exactly one of `done`, `cancelled`, `error`, and the run registry's
  final state agrees (`completed` / `cancelled` / `failed`). A Stop that arrives after the steps ended (while the reply
  is being stored, or later) changes nothing: the run ends `completed`.
- **R5 What a stopped run stores.** Stop before the user message is stored (waiting for the lock or a slot): nothing
  is stored and the day's message slot is given back (U1). Otherwise: the user message and exactly one assistant
  message with status `cancelled`, holding only text that already passed ReplyGuard (no template, no click hint) and
  the cards of tool calls that finished before the Stop.
- **R6 No booking authority from a stopped run.** Quote and booking-request cards of a `cancelled` message are never
  presented: no nonce, `bookable` false, the quote stays `quoted` (then `expired`); a click on it is `not_presented`. Only a later *completed* turn can present
  it (e.g. `request_booking` shows its full card with `done`). Stop
  never creates, changes or reverts a ticket or another quote's status. A new quote in a later turn is bookable as usual.
- **R7 Well-formed history.** Every tool call of a stored completion has a `tool_calls` row: a call interrupted by Stop
  or never started is stored with error `cancelled`. The next turn's context gives the model `{"code":"cancelled"}` for
  those calls, never their result; aborted model calls never enter the context.
- **R8 Idempotent.** Repeating `stop` for the same run returns the same state and changes nothing more.
