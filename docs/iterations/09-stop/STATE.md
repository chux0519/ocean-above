# 09-stop S1 — STATE (2026-11-24, updated after the Fable review)

Issue #48, plan `docs/iterations/09-stop/plan.md` (decisions 1–5 followed as written). Branch `it09-stop`.
Coordinator approval (hc, 2026-11-24): `airline/service.py` gets only `present` / `status` keyword arguments with
defaults that keep today's behaviour, plus store migration v4.

## Done
| Unit | Commit | What |
|---|---|---|
| Rules + contract | `6a1af68` | `state_invariants.md` group **R (R1–R8)**, `stop_turn` in the event alphabet, Q2 wording; contract **v1.6** (additive): `X-Run-Id` header, `run_id` on the first non-ping event, terminal `cancelled` event, `POST /api/conversations/{id}/runs/{run_id}/cancel`, `Message.status`, how to draw a stopped reply; fixtures get `status`, new `sse_cancelled_turn.txt` |
| Backend | `9bed8c0` | run registry + `Agent.cancel_run`, `TurnStream.run_id`, cancel endpoint, stopped-turn persistence, store v4, context for stopped turns, tests |
| Frontend | `7189ce0` | Stop button (square, 44×44, replaces Send while a reply runs), cancel + abort, "Stopped" marker, stopped-card note, no stale updates after switching conversations, vitest |
| Screens | this commit | `screens/` 1440×900 and 390×844, light and dark: `*-1-waiting-stop`, `*-2-stopped`, `*-3-next-message` |

### Backend design (as built)
- **Run lifecycle** (`loop.py` `Run`): `queued` (lock / per-user / slot wait; nothing stored) → `running` (set right
  before the user message is stored) → `finishing` (steps ended) → `completed` | `failed`; or `queued` / `running` →
  `cancelling` → `cancelled`. The final state is set by the terminal event itself (`emit`), so registry and stream
  cannot disagree (R4). Finished runs are kept (last 1024) so repeated / late Stops answer their final state.
- **Single-worker registry**, documented in `Run`'s docstring: with several workers a Stop could reach a process that
  does not run the turn; needs a shared coordinator (cancel flag in app.db / Redis polled at step boundaries, or
  sticky routing by conversation).
- **Stop while queued**: the run task is cancelled (or, if it has not started, `_run` sees the state); lock / slot /
  per-user count unwind; `on_not_stored` refunds the day's message slot; `cancelled {message: null}`.
- **Stop while the user message is stored / context built**: flag only (the task is not cancelled while an untracked
  write may run); `_turn` sees it and starts no step.
- **Stop during steps**: `turn.cancelled = True` + `steps_task.cancel()` (SDK closes the model stream; same path as
  08's `Agent.cancel`). Extra cooperative checks: before each model request node and before / after each tool thread,
  so a library that swallowed a `CancelledError` still cannot start work (R2). Any accepted Stop ends `cancelled`,
  even if the steps ended in the same instant.
- **Persisting a stopped turn** (`_finish_stopped`, after `Threads.drain`): tool calls of the step without a result
  are stored as `cancelled` rows (`cancelled: interrupted` for the one whose thread ran, `cancelled: not run` for the
  rest), result `{"code":"cancelled",…}` — the interrupted tool's real result is discarded (decision 2; its quote row
  stays `quoted`, never shown). Then `post_assistant_message(text=released text, cards=finished tools' cards,
  guard_problems=[cancelled], present=False, status="cancelled")` and `cancelled {run_id, message}`. No template, no
  click hint. The step's calls are registered before the completion is logged, so a Stop during that write still
  produces rows for every logged call (R7).
- **Context** (`context.py`): a cancelled assistant message contributes its text (if any, no reasoning) and a system
  note `[the user stopped the previous reply; …]`; no empty assistant message.
- **Observability**: `agent.run.stop_requested` / `agent.run.cancelled` log lines with `stop_to_end_ms`,
  `tokens_after_stop` (charged after the Stop, e.g. the aborted call's estimate) and `drain_ms` (spans in #35).

## Race boundary → test (all in `backend/tests/test_stop.py` unless named otherwise)
| Boundary (issue) | Test |
|---|---|
| before the user message is stored (task not started) | `test_stop_before_the_run_task_begins_stores_nothing_and_refunds` |
| before the user message is stored (lock wait) | `test_stop_while_waiting_for_the_conversation_lock_stores_nothing_and_refunds` |
| queued for a slot | `test_stop_while_queued_for_a_slot_stores_nothing_and_frees_the_queue` |
| while the user message is being stored | `test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step` |
| during model streaming (endless fake stream) | `test_stop_during_an_endless_model_stream_closes_it_and_keeps_the_released_text`; `test_agent_runtime.py::test_cancel_while_the_model_streams_closes_the_stream_and_ends_the_turn` |
| retry backoff (no retry after Stop) | `test_stop_during_the_retry_backoff_makes_no_further_request` |
| between model and tool | `test_stop_between_model_and_tool_runs_no_tool_and_keeps_the_history_well_formed` |
| during sync tool / DB work | `test_stop_during_sync_tool_work_waits_for_the_thread_before_releasing`; `test_agent_runtime.py::test_cancel_during_a_tool_waits_for_its_thread_and_runs_no_later_tool` |
| finished tool's card, booking authority | `test_a_stop_after_a_finished_tool_keeps_its_card_but_never_makes_it_bookable` (book → `not_presented`; a new quote in a later turn books) |
| while persisting the assistant reply | `test_stop_while_the_reply_is_being_stored_changes_nothing` (`finishing` → `done`) |
| after completion | `test_stop_after_completion_reports_completed_and_changes_nothing` |
| repeated Stop | `test_repeated_stop_is_idempotent_and_ends_once` |
| two tabs / old run id vs newer run | `test_two_tabs_an_old_run_id_never_stops_the_newer_run` |
| SSE disconnect without Stop | `test_a_disconnect_without_stop_lets_the_run_finish_and_store` |
| cross-user, wrong conversation, unknown run | `test_another_user_another_conversation_or_an_unknown_run_cannot_stop`; HTTP: `test_http_stop_is_owner_only_and_404_for_unknown_runs` |
| Stop/finish race → exactly one terminal outcome | model-based: `test_stop_at_a_random_point_keeps_every_invariant[0..39]` (R2, R4, R5, R6, R7 after every run; lock/slot/registry released) + `test_the_seeds_reach_every_stop_boundary` |
| end to end over HTTP | `test_http_stop_ends_the_stream_cancelled_and_the_reload_shows_it` |
| booking model (B/Q + R6) | `test_booking_model.py`: event `stop_turn`, named cases "a stopped turn's quote is never presented; a new quote is bookable (R6, B2)" and "a quote presented earlier stays bookable on its own card after a stopped turn shows it (R6, Q2)", mutant `PresentsStoppedTurns` caught as R6 |
| migration | `test_store.py::test_a_v3_database_is_migrated_to_v4_and_old_messages_are_completed` |
| frontend | `frontend/tests/stop.test.tsx` (Stop while waiting for the first token; Stop before headers; stopped before stored; grace-period abort without a network error; switching conversations detaches without Stop; reducer; marker; no Confirm on a stopped card) |

Mutation check (manual, reverted): `present=True` in `_finish_stopped` → 6 failures; no refund on queued Stop → 3 failures.

## Rewritten tests
| Test | Old assertion | New assertion | Why |
|---|---|---|---|
| `test_agent_runtime.py::test_cancel_while_the_model_streams_…` | ends `replace` + `done` (template) | ends `delta` + `cancelled`; stored text = released sentence, `status = cancelled` | #48: a Stop keeps validated text, terminal `cancelled` |
| `test_agent_runtime.py::test_cancel_during_a_tool_waits_…` | ends `done`; no `tool_calls` rows | ends `cancelled`; rows `cancelled: interrupted`, `cancelled: not run` | decision 2 / R7 (P2-7 settled) |
| `test_agent_loop.py::test_a_turn_that_cannot_get_the_lock_reports_busy`, `test_agent_concurrency.py` (2), `test_runtime_review.py` (3) | first-event `error` data equals `{code, message}` | same, after `first_data` / `first_sse` check and strip `run_id` | contract v1.6: the first event carries `run_id` |
| `test_limits.py::test_a_v1_database_is_migrated_…` | schema 3 | schema 4 (also drops `messages.status` to fake v1) | migration v4 |
| `test_booking_model.py::ev_click_concurrent` | B5 check first | B7 (quote booked once a ticket exists) checked before the B5 loser codes | the new event shifts the seeded sequences; `TicketWithoutStatusChange` was then first seen through its symptom (B5) instead of its cause (B7). Stronger, not weaker |

## Verification (source state `it09-stop` at the commit of this file)
- Backend: `bash tool/test_diff.sh` → no new failures (backend 830 + new tests; frontend 98 vitest); `python3 tool/review_lint.py` ok.
- `cd backend && uv run python -m evals --mode fake` → 31/31.
- Real-model evals N=1 (run by the coordinator on `8f0990d`, `--max-cost 0.25` accepted by the user; the runner's
  upper-bound estimate was $0.20): `deepseek-flash`, effort high, 31 cases / 68 turns — **31/31**, trajectory 100%,
  safety invariant violations **0**, ReplyGuard fallback 1.5% (1 reply, ≤ 5%), every tool 100%; cost **$0.0219**
  (08: $0.0229). Report: `docs/iterations/09-stop/evals/real-n1/report.md` (+ `results.json`).
- Real backend + Next proxy (dev, `docs/iterations/09-stop/slow_server.py`, scripted slow model): `X-Run-Id` arrives
  through the Next rewrite; Stop after the SSE reader disconnected still cancels and stores `cancelled`.
- Screens reviewed: Stop is a filled square in the round primary button (44×44 measured); stopped reply shows the
  quiet "■ Stopped" marker and the card note "Stopped before confirmation — ask again to book", no Confirm button, no
  red strip; the next message runs normally; no horizontal scroll at 390. (The purple widget at the right edge is a
  browser extension of the screenshot browser, not the app.)
- Impeccable: `npx impeccable detect` on the three changed components: no findings (regex rules only). The URL scan
  did not finish (the chat page needs a session); not rerun.

## Open / for the coordinator
1. ~~Real-model evals N=1~~ done by the coordinator (above).
2. Fable race review (R in the plan) — in progress; no code changes until it is in.
3. DESIGN.md (coordinator-owned) — draft for §composer / components:
   > composer: while a reply runs, Send becomes **Stop** (same round primary button, filled 14 px square, label
   > "Stop"; "Stopping" and disabled after the click). A stopped reply ends with a quiet `ink-2` marker "■ Stopped"
   > (no colour, not an error); its quote cards show no Confirm button and the line "Stopped before confirmation — ask
   > again to book". Stop before the message was stored: "■ Stopped — your message was not sent" under the composer,
   > input kept.
4. ~~Known edge: Stop + time limit may end `done`~~ — wrong: the review could not reproduce it and the code ends an
   accepted Stop `cancelled` (`stopped = stopped or turn.cancelled`, now pinned by
   `test_p2_9_a_stop_accepted_while_the_last_step_ends_still_ends_cancelled`). Removed.
5. Pre-existing (not changed): the typing indicator "Looking up routes…" stays visible after a card arrived until the
   first text (seen in `*-3-next-message`).

## Review (Fable, `review-stop.md`): finding → fix → test
No P0 / P1; 9 × P2, all fixed (commit `87c0e61`). Mutation checks re-run after the fixes: every mutation below is
killed (backend script `/tmp/oa-stop/mutate.py`, frontend `/tmp/oa-stop/fmutate.py`, both restore the file).

| Finding | Fix | Test (mutation that it kills) |
|---|---|---|
| **P2-1** Stop during the `server_busy` refund: no terminal event (probe 1) | the refusal branches (`ServerBusy`, `_NotStored`) set the run to `finishing` before their first await, so a Stop there is "too late" and does not cancel the task; `finally` adds a backstop `error internal_error` when a run ended without a terminal event (e.g. a shutdown cancel) | `test_p2_1_a_stop_while_a_server_busy_refusal_refunds_still_ends_with_one_terminal_event` (kills "refusal stays queued"); `test_p2_1_a_run_cancelled_by_a_shutdown_still_ends_with_a_terminal_event` (kills "no backstop") |
| **P2-2** finished runs kept the whole turn (probe 2) | `_retire` drops `run.turn` and `run.task`; only id / owner / conversation / state stay | `test_p2_2_a_finished_run_keeps_no_turn_context` (kills "keep turn") |
| **P2-3** Stop on a slow drain: Send back before the reply was stored | after the reader is closed without a terminal event, the client polls the (idempotent) cancel endpoint every 1 s until `cancelled` / `completed` / `failed` / 404 (max 35 s), keeping "Stopping"; then reloads | `stop.test.tsx` "waits for a slow stopped run to settle before restoring Send" (kills "no settle") |
| **P2-4** headers never arrive → stuck "Stopping"; Stop during chat creation still sent | the grace timer starts at the click (aborts even without a run id); after `createConversation` a pressed Stop sends nothing (input kept, "not sent" note) | "closes the request when its headers never arrive…" (kills "grace only after cancel"); "Stop while a new chat is being created sends nothing" (kills "send after Stop during create") |
| **P2-5** focus lost when Send ↔ Stop swap; no announcement | one `<Button>` element for both (type / label / icon / handler change), `aria-disabled` instead of `disabled` while stopping; focus returns to the input after a Stop if it was on the button; one polite live region ("Stopping the reply…", "Reply stopped.", "Stopped. Your message was not sent."); Esc in the input stops | "keeps the focus on the one Send/Stop button and returns it to the input after Stop; Esc stops" (kills "no focus return", "no Esc", "no announcement") |
| **P2-6** switching test could not catch regressions | test now has stored text in the old chat, waits for every fetch, checks no old text / marker / Stopping, Send works and the next reply gets a fresh Stop. Mutation F5 showed a **real bug**: `load()` sets the view itself, so a slow reload of the old chat replaced the new chat's view; the post-stream reload now checks `here()` before setting state | "switching conversations detaches the stream…" (kills F1, F6); new "a reload that lands after switching conversations does not replace the new view" (kills F5) |
| **P2-7** v4 → v3 rollback cannot start | no code change (the migration is correct); release + rollback steps drafted below for deploy.md and design.md §7 | — (operational) |
| **P2-8** model not told that stopped cards cannot be confirmed | `STOPPED_NOTE` adds: "Quote cards in that reply cannot be confirmed: to book one of its quotes, call request_booking for it again…" (request_booking on a `quoted` quote presents it in a completed turn, B2 unchanged). Not added as a fake eval: the fake model is scripted, so it would only test the script; the note reaching the model is what code controls | `test_p2_8_the_model_is_told_that_a_stopped_replys_cards_cannot_be_confirmed`; real evals N=1 after the change 31/31 |
| **P2-9** second-line checks untested (M1–M5, M10 survived) | tests accept a Stop *without* cancelling the task (what a swallowed `CancelledError` looks like) at each boundary; also fixed an order bug they exposed: after the tool thread the check now runs before `in_flight` is cleared, so a tool that ran is stored `cancelled: interrupted`, not `not run` | M2 `test_p2_9_a_lost_cancellation_during_a_tool_thread_discards_its_result_and_stops`; M4 `…before_a_tool_runs_no_tool`; M3 `…after_a_tool_starts_no_further_model_request`; M5 `…stop_accepted_while_the_last_step_ends_still_ends_cancelled`; M1 `…stop_in_the_instant_the_steps_ended_is_finishing_and_the_reply_completes`; M10 `test_p2_9_nothing_is_sent_after_the_terminal_event` |
| minor: contract `cancelling` / `finishing` outcomes; `RunStatus` | contract: `cancelling` may end `error` if storing fails; `finishing` / `completed` may end `error`; `RunStatus` = the five values the endpoint returns (TS type synced) | — |
| minor: switching back to a running chat cannot Stop | not changed (needs the active run in the conversation detail; no such capability before this PR) — proposed follow-up | — |

Verification after the fixes (`87c0e61`): backend full suite + `tool/test_diff.sh` no new failures; vitest 103;
lint ok; evals fake 31/31; real-model evals N=1 (`--max-cost 0.25`): **31/31**, trajectory 100 %, safety violations 0,
fallback 0.0 %, **$0.0223** (`docs/iterations/09-stop/evals/real-n1-review/`). Screens not re-shot: the visible states
are unchanged (the Stopping state now uses `aria-disabled` + 50 % opacity instead of `disabled`, same look).

## Drafts for docs the coordinator merges

### docs/deploy.md — release with a schema change (review P2-7)
> **App.db schema v4 (09-stop, #48).** The first start of this release migrates app.db to v4 (`messages.status`, old
> rows = `completed`; one transaction). Older images refuse a newer schema (`RuntimeError: … has schema 4, this code
> knows 3`) and do not start. Release: copy `app.db` (with `-wal` / `-shm`, or `sqlite3 app.db ".backup app.db.v3"`
> while the API is stopped) before `up -d`. Rollback to a v3 image: stop the API, then either restore the copy
> (loses rows written since) or downgrade in place: `ALTER TABLE messages DROP COLUMN status; PRAGMA user_version = 3;`
> (stopped replies then read as normal assistant messages), then start the old image.

### design.md §7, after the schema list
> v4（#48）：`messages.status`（`completed` | `cancelled`，旧行默认 `completed`）。迁移只向前；旧版本镜像遇到更新的 schema 拒绝启动，
> 回滚步骤见 deploy.md（先备份，或 `DROP COLUMN status` + `user_version = 3`）。

### design.md §4.10, replace the "取消" bullet
> - 取消（#48）：只有 Stop 接口（`POST /api/conversations/{id}/runs/{run_id}/cancel`）能停止一轮；断开 SSE 不算（照常跑完落库）。
>   每轮有 app 自己的 `run_id`（响应头 `X-Run-Id` + 第一个事件），进程内登记（单 worker；多 worker 需要共享的取消标志或按会话粘性路由）。
>   状态 `queued → running → finishing → completed / failed`，或 `→ cancelling → cancelled`，终态由终止事件决定（R4）。
>   排队时 Stop：什么都不存、退还当天额度。之后的 Stop：不再发起 LLM 请求、重试和工具调用，模型流立即关闭；已开始的工具线程跑完、
>   drain 后，只存已通过 ReplyGuard 的文字和已完成工具的卡片（`status = cancelled`，卡片不 presented、无 nonce），没结果的工具调用存为
>   `cancelled` 行（下一轮上下文里是 `{"code":"cancelled"}` + 一条 system 说明）。Stop 在落库阶段或之后到达不改变任何东西。
>   将来可选模式：`after_turn`（当前步结束后停）。规则 R1–R8 见 state_invariants.md。

### design.md §6, after the B7 paragraph
> **Stop（#48，R6）**：被用户停止的轮次不产生出票权限：它的 quote / 确认卡随 `cancelled` 消息存储但不 presented（无 nonce、不可点），
> quote 保持 `quoted` 直到过期；用户需要在新的轮次里重新询价或请求确认。Stop 不出票，也不撤销已出的票或已展示的报价。

### design.md §4.7 table, add a row
> | `cancelled` | 停止后存下的回复（或 `null`：用户消息还没存） | 用户按 Stop 后的终止事件 |

### README, after the intro paragraph
> Users can **Stop** a reply while it is generated: the server cancels the agent run (no further model or tool work)
> and keeps only the text and cards produced so far, marked as stopped; nothing from a stopped reply can be booked.
> Closing the tab does not stop a reply — it finishes and appears on reload. API: contract v1.6 in
> [docs/api/contract.md](docs/api/contract.md).
