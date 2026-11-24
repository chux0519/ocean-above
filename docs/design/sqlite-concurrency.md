# app.db on SQLite — concurrency limits, benchmark, migration criteria (#41)

Linked from `docs/design/design.md` §11. English, as requested for the final design doc. Measured numbers: see
[results/](../iterations/08-runtime/sqlite/results/) — they hold for that machine and that run only.

## X.1 What is serialized, and what is not

- **One writer for the whole file.** app.db runs in WAL mode. Readers never wait for the writer
  and see a consistent snapshot. Every write is one `BEGIN IMMEDIATE` transaction
  (`Store.write()`), which takes SQLite's single write lock for **the whole database file** —
  there are no row or table locks. Two writes that touch unrelated rows (a chat log of user A, a
  ticket of user B) still run one after the other.
- **This is not global request serialization.** Only the write transactions queue, and each is
  short (one insert or a booking check-and-insert, about a millisecond). HTTP requests, LLM
  streaming and route computation run concurrently around them; a chat turn holds the write lock
  for a few milliseconds spread over about seven separate small transactions (message slot, user
  message, LLM call logs, tool log, quote, assistant message), never across an LLM call.
- **The per-conversation lock** (`airline/locks.py`) serializes the chat turns and booking clicks
  of **one conversation** only; other conversations are not affected. It is an in-process
  `threading.Lock` map: it does **not** coordinate a second uvicorn worker, process or machine.
  The deployment therefore runs exactly one worker (design §8.3); with more, two turns of one
  conversation could interleave.
- **What the database guarantees without that lock.** One ticket per quote (B5,
  `tickets.quote_id UNIQUE`), booked ⇔ one ticket (B7, one transaction), terminal quote states
  (triggers) and single-use nonces hold through `BEGIN IMMEDIATE` and constraints alone, also
  across processes. What the lock adds is application ordering: a click never lands in the middle
  of a running turn, and two turns of one conversation never interleave their messages. Those
  semantics would need a database-level replacement (advisory locks, a conversation version
  column) before running more than one worker.

## X.2 Two different "busy" conditions

| | SQLite lock wait | Conversation busy | Server busy |
|---|---|---|---|
| What waits | one write transaction, for app.db's write lock | a turn or click, for its conversation's lock | a turn, for one of `AGENT_MAX_ACTIVE_TURNS` slots |
| Bound | `busy_timeout` 10 s per statement; plus a 10 s operation deadline per connection | turn 10 s, click 5 s; per user: 2 turns, 2 clicks in progress | `AGENT_TURN_QUEUE_SECONDS` (5 s) |
| On timeout | `StoreUnavailable`, rolled back → HTTP 503 `data_unavailable` (click: nothing changed, retry works) / turn falls back to the template | SSE / HTTP 409 `conversation_busy`, nothing stored | SSE `server_busy`, nothing stored, message slot returned |
| Scope | every writer of the file | one conversation (one user) | whole server |

(The issue text says `timeout=30`; the code has used 10 s since 06 P0b, design §4.9.)

## X.3 `_recheck()` inside the write transaction

`Airline.book` → conversation lock → `Store.write()` (`BEGIN IMMEDIATE`) → `_book()` →
`_recheck()`. B6 re-validates the quote on the current `locations.db`: strict content hash, and a
graph rebuild when the hash changed, then the itinerary price and a fresh cheapest-route search —
all **while app.db's write lock is held**. Since 06 P2-7 a changed file is normally loaded just
before the transaction, so inside it only the hash and the route search run (sub-millisecond on
711 cities). If the file changes between that pre-load and the re-check, the full rebuild
(~130 ms measured) happens inside the transaction and every other writer — messages, call logs,
other bookings — waits for it. The benchmark forces exactly that case (`changed inside
transaction`) to measure it.

**Candidate optimisation, not implemented (needs its own review against B1–B7):** run the
re-check before `BEGIN IMMEDIATE` and keep only a cheap, consistent check inside:
1. Before the transaction: `net = networks.current(strict=True)`; compute `(fingerprint,
   problem) = recheck(quote, net)`. The quote's legs, price and origin/destination are immutable
   once stored, so this depends only on the quote and `net`.
2. Inside the transaction: all state checks stay where they are (B1–B5: owner, status, expiry,
   nonce — they read rows another writer may change). Then re-hash `locations.db` (strict, no
   rebuild): if the hash still equals `fingerprint`, the pre-computed result is the result of a
   re-check made now; if it differs, either redo the full re-check inside (today's behaviour, rare)
   or refuse with a retryable `data_unavailable` and let the click be repeated.
3. **TOCTOU:** the window "file changes after the in-transaction hash, before COMMIT" exists
   today as well (B6 means "fresh at booking time", the time of the hash); the window "file changes
   after the pre-computation, before the transaction" is closed by step 2's hash comparison. The
   quote row cannot change in between except its status, which is still checked inside. So B6
   keeps its meaning, while the write lock is no longer held across a rebuild or route search.
   Cost: one extra hash of a 60 KB file per click.

## X.4 Why this is a conscious trade-off

SQLite + one worker gives atomic booking with one transaction and two constraints, no database
server to run, back up or secure, and an operational footprint of one file — the right size for a
take-home and a single-VPS demo. It is **not** a measured production scalability guarantee: the
benchmark below shows where it holds on one machine, not what a real airline workload needs.

## X.5 Benchmark (`backend/bench/`)

Reproducible, isolated, no LLM cost: `cd backend && nice -n 10 uv run python -m bench`
(`--quick` for a smoke run; `tests/test_bench.py` runs a tiny one in CI).
- **Setup:** per level a fresh app.db and a private copy of `locations.db` in a temp dir; fixed
  seed; 100 users with a conversation, history and presented quotes with nonces; the real app in
  its own uvicorn process (one worker, production limits) with a deterministic fake LLM (one
  `quote_route` call + a reply per turn, so each turn writes what a real one does).
- **Instrumentation:** `app.store.db.set_timing_hook` (off by default; one global lookup per
  operation when off) reports per transaction the `BEGIN IMMEDIATE` wait, hold time to COMMIT,
  total time and outcome (commit / rollback / busy / timeout), tagged with the calling function.
  `_recheck`, `NetworkProvider.current` and graph rebuilds are timed by wrappers installed in the
  bench server process only (no airline code changes).
- **Scenarios:** mixed 70 % reads (history, tickets, conversation list) / 20 % chat turns / 10 %
  bookings, closed loop, 1/5/10/25/50/100 clients, warm-up + 3 measured intervals; booking burst
  (all clients click distinct quotes at once); same conversation (4 identical clicks per quote at
  once, 3 concurrent turns per conversation, a turn and a click at once); `locations.db` unchanged
  / changed before the click / changed inside the booking transaction; the mixed load with a
  simulated model latency per LLM step; an in-process writer microbenchmark.
- **Metrics:** throughput, p50/p95/p99 per endpoint, write-lock wait and hold p50/p95/p99 (also
  per call site), `_recheck` and rebuild p50/p95/p99, BUSY / timeout / 5xx counts, server CPU and
  RSS, WAL size, and integrity checks that must be 0: double bookings, booked without ticket,
  ticket without booked quote or used nonce, client 200 without ticket (and vice versa),
  interleaved turns.

**Measured** (one run, 2026-11-24, Apple M4 10-core, macOS, Python 3.12, SQLite 3.47, APFS; full
tables in [results/bench-2026-11-24.md](results/bench-2026-11-24.md); the machine also ran other
agents, so treat single numbers as ±30 %):
- **Correctness held everywhere:** 0 double bookings, 0 quote/ticket mismatches, 0 client 200s
  without a ticket, 0 interleaved turns, 0 BUSY, 0 5xx — over the whole sweep, bursts of up to 300
  simultaneous clicks, 4 identical clicks per quote (exactly one success each; the rest
  `already_booked` / `conversation_busy`) and 3 concurrent turns per conversation.
- **Write transactions are short:** hold p50 ≈ 0.5–1.6 ms, p99 ≤ 34 ms at every level;
  ~250–290 write transactions/s at 25–100 clients. The in-process microbenchmark reaches
  ~3,000 commits/s with one insert per transaction.
- **The tail is lock *waiting*, not lock holding.** Write-lock wait p99 grows to 260–580 ms at
  10–25 closed-loop clients (and 600 ms with 64 threads in the microbenchmark, where hold p99 is
  0.4 ms). SQLite's busy handler polls with growing sleeps instead of queueing waiters, so an
  unlucky writer sleeps while others take the lock. Cheap remedy if it ever matters: an
  in-process FIFO lock in front of `BEGIN IMMEDIATE` (valid only with one worker).
- **The process, not SQLite, saturates first.** Throughput peaks at ~200 req/s at 50 clients with
  the single worker at 150–340 % CPU (Python, JSON rendering of growing histories, the 32-thread
  executor); at 100 clients it drops to ~160 req/s while write hold stays ~0.5 ms. With 1 client,
  one conversation grows by ~100 turns during the run, so history/chat latency there measures
  history size more than concurrency.
- **`_recheck` is cheap unless the network is rebuilt:** p50 0.2 ms with `locations.db`
  unchanged. A rebuild takes ~140 ms; when it happens inside the booking transaction (forced in
  30 of 32 bookings in the `changed inside transaction` mode, and 3 of 26 rebuilds even in
  `changed before click` because of concurrent changes) it holds the write lock ~140–195 ms, and
  other writers' lock wait p99 rises from 0.47 s to 2.8 s. That is the case X.3 removes.
- **With model latency (0.6 s per LLM step, simulated):** at 10 clients the app is model-bound
  (chat p50 1.3 s, write-lock wait p99 47 ms); at 50 / 100 clients 16 % / 49 % of turns get
  `server_busy` from the 8 turn slots long before app.db matters (write wait p99 40 / 196 ms).
- Timing hook overhead when on: within noise (~423 vs 429 µs per read transaction; a read
  transaction costs ~0.4 ms, mostly opening the connection).

## X.6 When to change what

All thresholds below are **illustrative service SLOs** for deciding, not measured results and not
requirements of the take-home. Measure on the target host with `python -m bench` against them.

| Decision | Do it when (example SLO, sustained at the expected peak) | What it costs / what to keep |
|---|---|---|
| **Keep SQLite as is** | integrity checks 0; write-lock wait p99 < 100 ms; booking p99 < 500 ms; 0 BUSY and 0 5xx; one worker's CPU is not the limit | nothing |
| **Shorten write transactions first** (X.3 re-check outside the lock; batch or move LLM / tool call logs out of the request path) | write-lock wait p99 above the SLO while CPU has headroom, or the measured `changed inside transaction` case breaks the booking SLO | review against B1–B7 and the TOCTOU argument in X.3; model test + regression test |
| **More than one worker / machine** | one process's CPU is saturated (the sweep's CPU column), or availability needs two instances | the per-conversation lock must move into the database (advisory lock or conversation version) first; SQLite on shared storage is not an option across machines |
| **Move app.db to PostgreSQL** | multi-instance is required, or sustained writes exceed what one SQLite writer serves within the SLO after the step above, or operations need managed backups / PITR / replicas | row locks help different quotes and conversations, but locking, idempotency (`ON CONFLICT`), transactions and schema migrations must be redone and re-verified (B1–B7); a server to run. A graph database or MCP does not address app.db write contention |

**Applied to the measured run (example SLO of the first row):** met at 1 and 5 closed-loop clients
(≈ 85 req/s, ≈ 18 chat turns/s, booking p99 93 ms, wait p99 63 ms); missed from 10 clients on by
the lock-wait tail and from 50 on by process CPU. With realistic model latency the turn slots
limit the load first (≈ 5–6 completed turns/s), and write-lock wait p99 stays ≤ 50 ms up to 50 clients.
**Decision for the demo: keep SQLite and one worker.** Next steps only if a measured target
workload needs them, in this order: (1) re-check outside the write lock (X.3) and/or a FIFO
writer lock; (2) more workers only after the conversation lock moves into the database; (3)
PostgreSQL when multi-instance or operations require it. No QPS threshold here is a fact about
production.
