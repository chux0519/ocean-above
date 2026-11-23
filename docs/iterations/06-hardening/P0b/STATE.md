# STATE — 06-hardening P0b: server-wide turn concurrency, thread / DB timeouts (issue #33)

- Branch `it06-concurrency` (based on main with P0a merged; origin/main merged in, incl. contract v1.4 `server_busy`).
  Tested source state: PR head. `HYPOTHESIS_PROFILE=ci uv run pytest -q`: 729 existing + 29 new
  (`tests/test_agent_concurrency.py`), all pass. `tool/test_diff.sh`: no new failures. `tool/review_lint.py`: ok.
- No new dependencies. Not touched: booking logic (`airline/`), ReplyGuard, prompt/context (#34), contract (coordinator
  added v1.4), frontend.
- Behaviour agreed with the coordinator before writing (hc, 2026-11-23): items 1–5 below, no per-tool timeout,
  booking-path SQLite timeouts → 503 `data_unavailable`.

## Done
| Item | Where | Test(s) (`tests/test_agent_concurrency.py`) |
|---|---|---|
| `TurnConcurrency` config: `AGENT_MAX_ACTIVE_TURNS` (default 8, 1–256), `AGENT_TURN_QUEUE_SECONDS` (default 5, 0–60); bad value fails startup | `agent/concurrency.py`, `api/settings.py` (`Settings.turn_concurrency`) | `test_concurrency_defaults_and_env`, `test_bad_concurrency_env_is_refused` (8), `test_bad_concurrency_values_are_refused` (4) |
| `TurnSlots`: FIFO counting semaphore on futures (no event-loop blocking; not bound to one loop, unlike `asyncio.Semaphore`); timeout → `ServerBusy`; cancel while waiting leaves no slot; hand-over/timeout race handled | `agent/concurrency.py` | `test_slots_queue_in_order_and_time_out`, `test_a_cancelled_waiter_leaves_no_slot_behind`, `test_slots_model` (hypothesis: random arrivals / holds / cancels; peak ≤ limit, every turn runs / refused / cancelled, count back to 0) |
| Order: conversation lock (10 s → `conversation_busy`), **then** a slot. A second message of a busy conversation takes no slot | `agent/loop.py` `_run` | `test_a_second_message_of_a_busy_conversation_takes_no_slot` |
| No slot in time → SSE `error {code: server_busy}` before `user_message`, nothing stored (no message, no title); `on_server_busy` callback runs before the event (even if nobody reads the stream); the API gives the day's message slot back | `loop.py`, `api/routes.py` | `test_n_plus_one_turns_the_last_gets_server_busy_and_nothing_is_stored`, `test_http_server_busy_gives_the_message_slot_back` |
| Queueing: a waiting turn runs when a slot frees | — | `test_a_queued_turn_runs_when_a_slot_frees_up` |
| Event loop stays responsive while a tool thread blocks and another turn queues (tick lag < 0.1 s) | — | `test_the_event_loop_stays_responsive_while_turns_wait_and_threads_block` |
| Thread tracking: blocking calls inside the turn's time limit (tool run, `add_tool_call`, `add_llm_call`) go through `Threads.run` (shielded `to_thread`). After the limit / an error, `_drain` waits up to `DRAIN_TIMEOUT` = 30 s for them **before** storing the reply and releasing the conversation lock and slot; longer → error log, turn ends anyway | `concurrency.py` `Threads`, `loop.py` `_turn` / `_drain` | `test_a_turn_cut_short_waits_for_its_tool_thread_before_storing_and_unlocking` (lock still held, slot still taken, booking click → `conversation_busy` while the thread runs; then template reply, quote of step 1 bookable, same click books), `test_a_thread_that_outlives_the_drain_limit_is_logged_and_the_turn_ends`, `test_threads_tracks_a_cancelled_call_until_it_ends`. Mutation: with `_drain` a no-op, the first two fail. |
| `Store`: busy timeout 30 → **10 s**; per-connection **operation deadline 10 s** (SQLite progress handler, checked every 1000 VM instructions; disarmed before COMMIT / ROLLBACK). Lock wait or deadline → `StoreUnavailable` (subclass of `sqlite3.OperationalError`), after rollback. Migrations at startup are not deadline-bound | `store/db.py` | `test_a_write_that_waits_too_long_for_the_lock_is_store_unavailable`, `test_a_long_operation_is_interrupted_and_rolled_back`, `test_store_timeout_defaults` |
| HTTP: `StoreUnavailable` → 503 `data_unavailable` (all endpoints; booking: nothing written, quote stays `presented`, same nonce books on retry). In a chat turn it takes the existing `internal_error` / template path | `api/errors.py` | `test_http_booking_on_a_locked_database_is_503_and_can_be_retried` |
| Booking path unaffected by slots: booking needs no slot; it shares only the conversation lock | — | covered by the cut-short test (book after the turn) |

## Limitations (precise; for design.md)
- **A worker thread cannot be stopped.** `asyncio.timeout` / cancellation around `asyncio.to_thread` only stops
  *waiting*; the thread runs to its end. Nothing in this code claims hard cancellation.
- What bounds a thread instead: app.db statements (busy wait ≤ 10 s per statement; SQL of one connection interrupted
  after 10 s wall time from its opening — a booking transaction that also re-reads locations.db counts that time too).
  **Not** bounded: reading `locations.db` (`core/network.py`, `sqlite3.connect` with the default 5 s busy timeout, no
  deadline — outside this issue's files) and CPU work (routing on the 100-city graph: milliseconds) and filesystem
  stalls.
- Consequence: a turn cut short by its 90 s limit may take up to `DRAIN_TIMEOUT` (30 s) longer before it stores the
  template reply and releases the lock/slot. If a thread outlives that too, the turn ends anyway (error log) and that
  thread may still write after the lock was released (e.g. a `quoted` quote that is never presented, hence never
  bookable; B5/B7 still hold through `BEGIN IMMEDIATE` and `tickets.quote_id UNIQUE`).
- Stuck threads keep their turn's slot, so at most `AGENT_MAX_ACTIVE_TURNS` agent threads exist at once (the default
  executor has ≥ 5 workers; with 8 slots a few may queue inside the executor; plain `to_thread` calls outside the turn
  limit are unchanged).
- Waiting for a slot holds the conversation lock: a booking click of *that* conversation waits ≤ 5 s
  (`BOOK_LOCK_TIMEOUT`) and may get `conversation_busy` (retryable).
- Single uvicorn worker: slots are per process.
- `conversation_busy` still keeps the day's message slot (unchanged, state_invariants U-rule); `server_busy` refunds it.

## Open points / for the coordinator
1. `.env.example`: add `AGENT_MAX_ACTIVE_TURNS=8`, `AGENT_TURN_QUEUE_SECONDS=5` (not in my files).
2. Frontend: handle `server_busy` like `conversation_busy` (keep the input text) — you said you'll schedule it.
3. `core/network.py` (locations.db reads) has no operation deadline; could get the same progress handler in a later issue.
4. Executor sizing: with `AGENT_MAX_ACTIVE_TURNS` raised far above the CPU count, `to_thread` calls queue in the default
   executor (min(32, cpu+4)); not a deadlock, only latency.
5. state_invariants.md suggestion — **C1**: at most `AGENT_MAX_ACTIVE_TURNS` turns hold a slot; a refused turn stores
   nothing and refunds its message slot. **C2**: a turn releases its conversation lock only after every worker thread
   it started has ended, or after `DRAIN_TIMEOUT` (logged). Model test: `test_slots_model`.

## Draft for docs/design/design.md (new §4.9 "并发与阻塞调用"; plus a §4.6 row)

### 4.9 并发与阻塞调用（06 P0b，#33）
- **全站并发上限**：同时运行的对话轮次最多 `AGENT_MAX_ACTIVE_TURNS`（默认 8，1–256），在每会话锁之外。先拿会话锁，再拿名额
  （先来先得），最多等 `AGENT_TURN_QUEUE_SECONDS`（默认 5 s，0–60）；等不到 → SSE `error server_busy`，在 `user_message`
  之前发出，什么都不存，并退还当天的消息额度。等待是 `await`，不阻塞事件循环。
- **线程不能强制中止**：工具和 app.db 调用在工作线程里跑（`asyncio.to_thread`）；超时只能停止等待，线程会跑完。所以本轮
  在时限内启动的线程都登记下来；整轮超时或出错后，先等它们结束（最多 30 s）再落 assistant 消息、释放会话锁和名额——
  否则线程可能在锁释放后还在写 quote。超过 30 s 记 error 日志后照常结束。线程占着名额，因此线程数有上限。
- **app.db 时限**：每条语句等锁最多 10 s（busy timeout），每个连接（一次读或写事务）从打开起 10 s 后中断正在跑的 SQL；
  两者都回滚并抛 `StoreUnavailable`。HTTP 一律 503 `data_unavailable`（出票：没写入任何东西，quote 和 nonce 不变，可重试）；
  对话轮次里走现有的 `internal_error` / 模板回退。
- 未覆盖：读 locations.db（无操作时限）、CPU 计算（路由毫秒级）、文件系统卡顿。

§4.6 加两行：
| 全站对话轮次已满 | 最多等 5 s；仍满 → `server_busy`，消息不保存，额度退还，前端保留输入 |
| app.db 锁等待 / 操作超时 | 回滚；HTTP 503 `data_unavailable`（出票可用同一 nonce 重试）；对话里走模板 / `internal_error` |

## How to verify
```sh
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_agent_concurrency.py tests/test_agent_loop.py tests/test_agent_budget.py
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
```
