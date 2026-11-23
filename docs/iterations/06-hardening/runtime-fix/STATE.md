# STATE — 06-hardening runtime review fixes (issue #43, report `../review-runtime.md`)

- Branch `it06-runtime-fix`. origin/main merged in (incl. #34: prompt/context/tools, token cap default 60000); no conflicts.
  Tested source state: commit `da406c3` (code); the commits after it change only docs.
  `HYPOTHESIS_PROFILE=ci uv run pytest -q`: **798 passed** (758 before; 25 new in `tests/test_runtime_review.py`,
  15 from main). `tool/test_diff.sh`: no new failures. `tool/review_lint.py`: ok. `test_runtime_review.py` was run 5× in a row
  with no flaky failure.
- No new dependencies. The booking logic and B1–B7 behave as before (`test_airline_booking.py` and `test_booking_model.py` unchanged and passing). Contract not changed (see open point 1).
- Files: `backend/app/agent/{loop,budget,llm,concurrency,context}.py`, `backend/app/airline/service.py` (book_async
  only), `backend/app/api/{routes,errors}.py`, tests (`test_runtime_review.py` new; `test_agent_budget.py` 3 tests
  rewritten for the new charging rule; `test_agent_concurrency.py` 2 tests given a per-user limit so they still fill the server-wide slots).

## Findings → fix → test (`tests/test_runtime_review.py` unless named)
| Finding | Fix | Test(s) |
|---|---|---|
| **P1-1** book clicks occupy executor threads while waiting for the lock | `book_async` waits with `hold_async` on the event loop; only the B1 read and the transaction (`_book_transaction`) run in threads. A cancelled click keeps the lock until its transaction thread ended. ≤ `BOOK_MAX_PENDING_PER_USER` = 2 clicks per user in progress; more → `conversation_busy` (409, retryable, nothing changed). Startup sets the loop's default executor to `2 × AGENT_MAX_ACTIVE_TURNS + 16` (default 32) (`api/routes.py size_default_executor`, `concurrency.executor_workers`). Sync `book` unchanged | `test_p1_1_book_clicks_waiting_for_the_lock_hold_no_worker_thread` (12 clicks, 4-thread executor: `to_thread` lag < 0.1 s; **old code: 2.84 s, fails**), `…_at_most_two_clicks_per_user_are_in_progress`, `…_a_cancelled_click_keeps_the_lock_until_its_transaction_ended`, `…_book_checks_keep_their_order`, `…_the_default_executor_is_sized_at_startup` |
| **P1-2** failed attempts charged request/2 + 4096 | `LLMError.received`: None when no response body was read (HTTP status error, connect/TLS failure, timeout before the response) → charged **0**; otherwise output characters received before the break → request/2 + received/2 (≤ 4096). Completed calls without usage: output from the completion's characters too. Full 4096 only when the output is unknown (the turn's own `llm_timeout`, a ReplyGuard cut, the turn time limit; reasoning is not visible there) | `test_p1_2_one_failed_attempt_does_not_turn_a_long_conversation_into_the_template` (default budget, 27k+ characters of context, 503 then success → LLM reply, not the template; old rule would charge > 17k), `…_the_client_reports_what_a_failed_call_received`; rewritten `test_agent_budget.py::test_failed_and_retried_calls_count`, `::test_missing_usage_is_charged_conservatively`, `::test_retries_can_exhaust_the_token_budget` |
| **P2-1** `total_tokens: 0` counted as free | `reported_tokens = max(total, prompt + completion)`; 0 → no report → estimate | `test_p2_1_a_reported_total_of_zero_is_not_free`, `test_agent_budget.py::test_token_helpers` |
| **P2-2** no token check between retries | after a failed attempt, `BudgetTracker.out_of_tokens()` → no retry, template `token_budget` | `test_p2_2_no_retry_starts_once_the_token_cap_is_reached`, `test_agent_budget.py::test_retries_can_exhaust_the_token_budget` |
| **P2-3** 300 tool calls → 300 refusal rows, 115k-character next request | completion with > `max_tool_calls_per_step × 4` (16) calls: none runs, no row per call, turn ends `tool_budget` (value = number of calls); the logged completion gets `tool_calls_not_run: true` and `build_context` skips it | `test_p2_3_a_completion_with_hundreds_of_calls_runs_none_and_stores_none` (next context < 5k characters), `…_up_to_four_times_the_step_limit_still_gets_per_call_refusals`; mutation (check disabled) → fails |
| **P2-4** LLM call interrupted by the turn limit: no record, no charge | `except CancelledError`: record `{"aborted": "turn_timeout"}` (estimate, full output) via `Threads.start` (tracked, `_drain` waits), then re-raise | `test_p2_4_a_call_cut_by_the_turn_limit_is_recorded_and_charged` |
| **P2-5** time limit landing on `add_tool_call`: row stored, card lost | the outcome, guard record, card and LLM message are registered as soon as the tool returns, before the row write | `test_p2_5_the_time_limit_on_add_tool_call_keeps_the_card_in_the_reply` (card in the template reply, bookable); mutation (old order) → fails |
| **P2-6** 20 000 too tight | handled by #34 (default 60 000, measured); checked against the history budget | `test_p2_6_a_three_step_turn_at_the_history_budget_fits_the_default_cap` (40k characters of history, 3 steps, realistic usage) |
| **P2-7** locations.db read inside the app.db write lock | `_book_transaction` loads a changed locations.db (non-strict `networks.current()`, errors ignored) **before** `BEGIN IMMEDIATE`; inside, the strict re-check (B6) only re-hashes the file. B6 is unchanged: the strict check still happens inside the transaction. A filesystem stall during that hash still holds the write lock (documented below) | covered by the booking suites (refusal order, stale, data_unavailable) |
| **P2-8** no per-user slot limit | `AGENT_MAX_TURNS_PER_USER` (default 2, 1–256): checked after the conversation lock, before the slot; over → SSE `conversation_busy` with its own text (`USER_BUSY`), nothing stored, the day's slot kept (like `conversation_busy`) | `test_p2_8_one_user_cannot_take_more_than_two_slots`, `…_turns_per_user_from_env` |
| **P2-9** a: `stream_turn` reads app.db on the loop | `owner_checked=True` (the route already checked in a thread) skips it | `test_p2_9_stream_turn_does_not_read_app_db_on_the_event_loop_when_the_owner_was_checked` |
| P2-9 b: waiting for a slot holds the conversation lock | not changed (documented): a click of that conversation waits ≤ 5 s on the event loop (no thread now) and may get `conversation_busy` | — |
| P2-9 c: `add_message` failure says "saved", keeps the slot | `internal_error` with `NOT_STORED` text, and `on_not_stored` (formerly `on_server_busy`, still accepted) gives the day's slot back | `test_p2_9_a_message_app_db_could_not_store_is_reported_as_not_saved_and_refunded` (HTTP) |
| P2-9 d: app.db timeout 503 says "Route data…" | `STORE_BUSY` text, same code `data_unavailable` | `test_p2_9_an_app_db_timeout_says_busy_not_route_data` |
| P2-9 e: site budget ignores the per-turn estimates | `llm_calls.tokens` = what the turn was charged (reported, estimated or 0); `response.usage` keeps the raw report | `test_p2_9_the_site_budget_counts_what_the_turn_was_charged`, `test_p2_4_…` |

### Test gaps from the review
| Gap | Test |
|---|---|
| 1 rollback after `mark_nonce_used` | `test_gap_1_a_booking_interrupted_after_the_nonce_was_marked_writes_nothing` |
| 2 realistic context / default budget | `test_p1_2_one_failed_attempt_…`, `test_p2_6_…` |
| 3 executor saturation | `test_p1_1_book_clicks_waiting_for_the_lock_hold_no_worker_thread` |
| 4 next context well-formed after a cut (tool and stream), cut on `add_tool_call` | `test_gap_4_the_next_context_is_well_formed_after_a_turn_cut_short`, `test_p2_5_…` |
| 5 drain timeout asserted exactly | `test_gap_5_a_thread_outliving_the_drain_ends_the_turn_with_llm_unavailable` (events, timing, lock, slot) |
| 6 disconnect + server_busy refund; real HTTP concurrency | `test_gap_6_server_busy_refunds_even_when_the_client_left`, `test_gap_6_http_server_busy_under_real_concurrency` |
| 7 `total_tokens: 0`, hundreds of calls | `test_p2_1_…`, `test_p2_3_…` |

## Open points / for the coordinator
1. **Contract wording** (no shape change): `conversation_busy` now also comes (a) from `POST /quotes/{id}/book` when a
   user has 2 clicks in progress, and (b) on the SSE stream when the user already has 2 turns running in other
   conversations (own message text). Suggest one sentence in `docs/api/contract.md` §booking errors and the SSE `error` row.
   `internal_error` may now also come **before** `user_message` (the message could not be stored; the slot is refunded) —
   the frontend should keep the input text there too.
2. `.env.example`: optional `AGENT_MAX_TURNS_PER_USER=2` (not in my files).
3. `docs/design/state_invariants.md`: suggest **C3** "a waiting booking click holds no worker thread; its transaction
   runs to its end before the conversation lock is released, even when the request is cancelled" and **C4**
   "at most `AGENT_MAX_TURNS_PER_USER` turns and `BOOK_MAX_PENDING_PER_USER` clicks per user at once".
4. The 60 000 cap covers 3 steps at the 40k-character history budget; near the 80k hard limit a 3-step turn may still
   reach it (template reply, safe). If that shows up in evals, lower `HISTORY_HARD_LIMIT` rather than raise the cap.
5. P2-9 b left as documented behaviour (changing the lock → slot order would let a queued second message of a
   conversation take a slot; see P0b).

## How to verify
```sh
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_runtime_review.py tests/test_agent_budget.py tests/test_agent_concurrency.py tests/test_airline_booking.py
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
```

## Drafts for docs/design/design.md

§4.8, replace the "token 软上限" bullet with:
- **token 软上限**：在两次 LLM 请求之间、以及每次重试之前检查（正在进行的那一步会跑完，超出后不再开始下一步，也不再重试）。
  每次调用按以下规则计费：服务商回报的用量取 `max(total_tokens, prompt_tokens + completion_tokens)`（0 不算回报）；
  **没读到任何响应体的失败**（HTTP 错误状态、连接 / 握手失败）计 0；其余没有可用回报的调用（流中途断开、
  completion 没带 usage）按估算计：`ceil(请求 JSON 字符数 / 2) + ceil(已收到的输出字符数 / 2)`（输出含文本、思考、
  工具调用，最多到单次输出上限）；输出量不可知时（本次调用超时、ReplyGuard 截断、整轮时限打断）按整个单次输出上限计。
  整轮时限打断的调用也落一条 `llm_calls`（`aborted: "turn_timeout"`）。每次计费写在 `llm_calls.response.budget`；
  `llm_calls.tokens` 存同一个计费值（服务商原始回报仍在 `response.usage`），全站每日预算（§8.2）按它累计，
  所以两者口径一致。**这不是严格的费用上限**：单次调用最多可以超出剩余额度一整步，价格也按模型而异。

§4.8, add after the "工具调用逐个计数" bullet:
- 一次 completion 的工具调用超过 `每步上限 × 4`（默认 16）时视为异常：一个都不执行、不逐条落拒绝，本轮以模板结束
  （`tool_budget`，值为调用数）；该 completion 在 `llm_calls` 里标 `tool_calls_not_run`，重建上下文时跳过它。
- 工具返回后，结果、卡片和给 LLM 的消息立即登记到本轮，再写 `tool_calls` 行；时限落在写行上时，模板回复仍带这张卡。

§4.9, add / replace:
- **每用户上限**：同一用户同时运行的轮次最多 `AGENT_MAX_TURNS_PER_USER`（默认 2，1–256），在会话锁之后、名额之前检查；
  超出 → SSE `error conversation_busy`（文案说明是其他会话在忙），什么都不存，不退额度。
- **出票点击不占线程等锁**：`book_async` 在事件循环上等会话锁（`hold_async`），拿到锁后才在工作线程里跑出票事务；请求被取消时
  也等事务线程结束再释放锁。每用户同时进行的点击最多 2 个，超出 → 409 `conversation_busy`（可重试，什么都没改）。
  出票事务开始前先加载已变化的 locations.db，事务内的 B6 严格复核只需重新哈希文件。
- **线程池大小**：启动时显式设置事件循环默认线程池为 `2 × AGENT_MAX_ACTIVE_TURNS + 16`（默认 32）：每个运行中的轮次
  同时最多用一个线程，被打断的轮次最多两个（未结束的工具线程 + 被打断调用的记录），余量给出票事务和路由的短读取。
- **消息没存上**：保存用户消息时 app.db 出错 → SSE `internal_error`（“没有保存”），在 `user_message` 之前，退还当天额度。
  app.db 超时的 HTTP 503 文案改为 “服务繁忙，没有做任何改动”。
- 未覆盖（替换原句）：读 locations.db 没有操作时限；出票事务里的严格复核仍在 app.db 写锁内哈希 locations.db，文件系统卡住时
  写锁被一直占着，其他写入 10 s 后 `StoreUnavailable`。CPU 计算（路由毫秒级）。等名额时持有会话锁：全站满载时，
  同会话的点击最多等 5 s（在事件循环上，不占线程）后得到 `conversation_busy`。

§4.6, add rows:
| 同一用户已有 2 个轮次在跑 | `conversation_busy`（其他会话忙），什么都不存 |
| 用户消息没能存入 app.db | `internal_error`（没有保存），额度退还 |
