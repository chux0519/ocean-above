# PR #54 评审：#48 用户 Stop（竞态评审，只读）

- 对象：分支 `it09-stop` @ `8f0990d`（`git diff origin/main...HEAD`）。评审者：Fable 5.2，2026-11-24。
- 结论：**没有 P0 / P1**。9 条 P2（2 条用探针复现，2 条用变异确认，其余为读代码确认或推断）。出票安全（B1–B7、R6）没有找到破口。
- 运行过的检查：
  - 后端 `HYPOTHESIS_PROFILE=ci uv run pytest -q`：892 passed。
  - 前端 vitest：在 `/tmp` 副本里跑（本 worktree 没有 `node_modules`，借用了 it09-stop worktree 的），94 passed；唯一失败的 `legal.test.ts` 是因为副本没带 `docs/`，与本 PR 无关。
  - `python3 tool/review_lint.py`：ok。
  - 探针：`/tmp/ocean-probe/test_probe.py`；变异检查：`/tmp/ocean-probe/mutate.py`（后端）、`fmutate.py`（前端），都只改 `/tmp/ocean-mut` 副本。
- 没做：真实 LLM、浏览器实测（前端结论来自读代码和 vitest）。

## 十个重点的结论

| # | 重点 | 结论 |
|---|---|---|
| 1 | Stop 不出票、不撤销票；停止轮次的 quote 不可订 | 成立。唯一的 `present=True` 路径是 `_finish`，只有 `turn.cancelled` 为假才到达；`steps_task` 结束后 `cancel_run` 不再接受 Stop（`loop.py:330`），所以 `_finish` 进行中 / presented 刚写完时的 Stop 一律返回 `finishing` / `completed`，回复按完成处理、卡片照常可订，行为有定义。nonce 需要 `quote_cards` 行（`service.py:476`），`present=False` 不写这行。被打断的报价工具：结果丢弃，quote 行留在 `quoted`，模型看不到它的 id。 |
| 2 | 只有一个终态 | 基本成立（`emit` 守卫 + 终态由事件决定）。例外见 P2-1：一个窗口里流**没有**终态事件。 |
| 3 | run_id / 跨用户 / 幂等 | 成立。按 run_id 查表并核对 user 和 conversation；路由先查会话归属；未知一律 404。 |
| 4 | 锁、名额、drain | 成立。排队时取消只会留下一个只读线程；`TurnSlots._acquire` 在“交接与取消同时发生”时归还名额；停止后的写都在 drain 之后、放锁之前。 |
| 5 | 断开 ≠ Stop | 成立（有测试）。 |
| 6 | 额度退还 | 成立。只在排队时 Stop / `server_busy` / `_NotStored` 三个互斥分支；`release_message` 按 reservation id 删除，重复调用也不会多退。 |
| 7 | 注册表 | 结束后都会 `_retire`；但保留的 1024 条带着整轮上下文（P2-2）。单 worker 的说明在 `Run` 的 docstring 和 Dockerfile `--workers 1`。 |
| 8 | 迁移 v4 | 安全：一个 `BEGIN IMMEDIATE` 事务里 `ADD COLUMN … NOT NULL DEFAULT 'completed' CHECK`，旧行自动为 completed。回滚注意见 P2-7。 |
| 9 | 前端 | 主流程正确；边角见 P2-3、P2-4、P2-5。 |
| 10 | 测试 | 后端较强，前端“切换会话后无陈旧更新”的测试抓不到回归（P2-6）。变异结果见文末。 |

STATE.md 的“已知边缘 4”（Stop 与轮次超时同时到达可能以 `done` 结束）我没能复现：探针 3 里结果是 `cancelled`，读代码也是（`loop.py:537` 的 `stopped = stopped or turn.cancelled` 在超时分支之后）。这条说明可以删掉或改写。

## P2

### P2-1 `server_busy` 退款期间的 Stop：SSE 没有终态事件 — CONFIRMED（探针 1）
- 位置：`backend/app/agent/loop.py:433-437`（`except ServerBusy` 里的 `await not_stored()`），配合 `loop.py:336-338`。
- 场景：全站名额已满，新一轮排队到 `queue_seconds` 超时 → 进入 `except ServerBusy`，在 `await not_stored()`（退款线程，几毫秒）处挂起，此时 `run.state` 仍是 `queued`。这时 Stop 到达 → `cancel_run` 返回 `cancelling` 并 `run.task.cancel()` → `CancelledError` 在这个 except 分支里抛出，同级的 `except asyncio.CancelledError` 接不到 → `finally` 把状态设为 `failed` 并结束队列。
- 结果（探针输出）：`('queued', 'cancelling', [], 'failed', [1])` —— 流里零个事件，违反 R4“恰好一个终态”；Stop 回答 `cancelling` 但注册表是 `failed`。退款执行了一次，没有泄漏锁或名额。前端此时静默结束（消息没发、输入保留、没有任何提示）。
- 建议：在进入各个拒绝分支前先把状态移出 `queued`（例如 `except ServerBusy` 开头设 `run.state = "failing"` 之类的不可取消状态），或者把 `not_stored()` 放进 `asyncio.shield` 并在外层兜底：`finally` 里若 `run.terminal is None` 且不是关机，就补发一个终态事件。加一个命名回归测试。

### P2-2 注册表保留的已结束 run 带着整轮上下文 — CONFIRMED（探针 2）
- 位置：`loop.py:361-366`（`_retire`），`Run.turn` / `Run.task`（`loop.py:124-125`）。
- 场景：每个结束的 run 进 `_finished`（最多 1024 条），但 `run.turn`（`messages` 上下文，硬上限约 8 万字符，加 guard 的 records、cards、`emit` 闭包 → 事件队列，断线时队列里是整轮未读事件）和 `run.task` 都没清掉。
- 结果：常驻内存 ≈ 1024 × 每轮上下文。短对话几 MB；长对话多的话可到一两百 MB，在小 VPS 上不小。不是无界泄漏。
- 建议：`_retire` 里 `run.turn = None; run.task = None`（cancel 接口对已结束 run 只用 `id / user_id / conversation_id / state`）。测试里加一条断言。

### P2-3 Stop 被接受但 3 秒内没收尾：前端恢复 Send，停止的回复不显示 — PLAUSIBLE
- 位置：`frontend/src/app/chat/page.tsx:234-239`（`requestCancel` 的 `STOP_GRACE_MS`），`page.tsx:222-224`。
- 场景：Stop 落在一个慢的工具 / app.db 写上（drain 最长 30 秒）。3 秒后前端 abort 并立刻 `load()`，这时服务器还没存下 `cancelled` 消息、会话锁也还没放。
- 结果：界面只有用户消息、没有“Stopped”标记；Send 已恢复，但下一条消息要等锁（最多 10 秒后 `conversation_busy`）。停止的回复要到下次刷新才出现。
- 建议：grace 结束后若还没收到终态，轮询一次会话（或再调一次 cancel 接口看状态是否到了 `cancelled`）再恢复 Send；至少在这种情况下显示本地的“Stopped”标记。

### P2-4 响应头迟迟不到时，Stop 按钮卡在禁用的“Stopping” — PLAUSIBLE
- 位置：`page.tsx:235`（没有 `runId` 就直接返回，不设定时器），`page.tsx:291`（`disabled={stopping}`）。
- 场景：POST 已发出但响应头没回来（网络卡住）。用户点 Stop：没有 run id → 不发 cancel，也不启动 abort 定时器。
- 结果：按钮一直是禁用的“Stopping”，直到浏览器自己超时；用户没有任何出路。相关的小问题：新会话还在 `createConversation` 时点 Stop，消息仍会发出去，然后才被取消（`page.tsx:174-178` 没有在创建后检查 `run.stop`）。
- 建议：点 Stop 时无条件启动一个兜底定时器（比如 10 秒后 abort）；`createConversation` 之后若 `run.stop` 就不再发送。

### P2-5 Stop 按钮的可访问性 — CONFIRMED（读代码）
- 位置：`page.tsx:290-295`，`frontend/src/components/message-list.tsx:10-16`。
- 问题：
  - Send 和 Stop 是条件渲染的两个不同按钮：用鼠标或键盘激活 Send 后焦点所在的按钮被卸载，焦点掉到 `body`；Stop 结束时同样（先禁用再卸载）。键盘用户要从页面顶部重新 Tab。
  - “Stopped”标记在消息列表里没有 live region，读屏听不到停止结果（只有“未发送”那条有 `role="status"`）。按钮从 “Stop” 变 “Stopping” 也不会被播报。
- 好的部分：`aria-label`、44×44、`type="button"`（不会误提交）、流式期间 Enter 不会再发送。
- 建议：保留同一个 `<Button>` 元素，只换 `type` / `aria-label` / 图标和处理函数；停止后把焦点还给输入框；“Stopped”标记加 `role="status"`（或消息区一个礼貌的 live region）。可选：Esc 触发 Stop。

### P2-6 前端“切换会话后无陈旧更新”的测试抓不到回归 — CONFIRMED（变异）
- 位置：`frontend/tests/stop.test.tsx:154-166`。
- 变异（都存活）：删掉 `page.tsx:199` 的 `if (!here()) return;`（流结束后的守卫，删掉后会把旧会话重新 `load` 到当前视图上）；删掉 `page.tsx:223` 重载后的守卫；删掉事件回调里的守卫；`detach` 不复位 `stopping`。
- 原因：假服务器的 `c_1` 返回空消息列表，且断言在陈旧的重载落地之前就通过了。
- 建议：让 `c_1` 重载返回带文字的消息，切到 `c_2` 后等所有 fetch 落地，再断言视图仍是 `c_2`、没有 `c_1` 的文字、Send 可用、没有“Stopping”。

### P2-7 回滚：v3 镜像无法在 v4 的 app.db 上启动 — CONFIRMED（读代码）
- 位置：`backend/app/store/db.py:307-308`；`docs/deploy.md` 没有相应说明。
- 场景：上线后发现问题想退回上一版镜像 → 旧代码看到 `user_version = 4` 直接抛 `RuntimeError`，起不来。
- 建议：发布步骤里加“先备份 app.db”，并在 deploy.md 写明回滚 = 恢复备份（或手工 `ALTER TABLE messages DROP COLUMN status` + `PRAGMA user_version = 3`）。迁移本身没问题。

### P2-8 给模型的“已停止”提示没说卡片不可订 — PLAUSIBLE
- 位置：`backend/app/agent/context.py:33`（`STOPPED_NOTE`）。
- 场景：停止的轮次里报价工具已完成，下一轮上下文里有它的 `quote_id` 和结果。用户说“订吧”，模型可能直接回答“请点卡片上的 Confirm”而不调用 `request_booking` —— 但那张卡没有按钮。
- 结果：不影响安全（没有 nonce 就订不了），只是用户被指向一个不存在的按钮。
- 建议：提示里加一句“那条回复里的报价卡不能确认；要订票需重新调用 request_booking”。可以加一条 fake eval。

### P2-9 R2 / R4 的协作式检查没有测试能抓到 — CONFIRMED（变异 M1–M5、M10）
- 位置：`backend/app/agent/loop.py:580-581`（模型请求前）、`650-651`（工具前）、`659-660`（工具线程返回后）、`537`（`stopped = stopped or turn.cancelled`）、`391-393`（终态后丢弃事件）、`330-331`。
- 场景：这些检查是为“SDK 或某个库吞掉了 `CancelledError`”准备的。现在的假 LLM 和 SDK 都会正常传播取消，所以逐个删掉后全部测试仍通过；SDK 升级后如果真的吞掉取消，这几行被人误删也不会有测试报警。
- 建议：加一个命名测试，用一个吞掉 `CancelledError` 的假流 / 假工具包装（或直接只设 `turn.cancelled = True` 不调 `steps_task.cancel()` 的测试钩子），断言之后没有新的 LLM 请求和工具调用、仍以 `cancelled` 结束；再加一个“终态后再 emit 被丢弃”的单元测试。

## 小问题（不分级）
- `docs/api/contract.md:140`：`cancelling` 说“流以 `cancelled` 结束”，但用户消息正在存储时的 Stop 若存储失败会以 `error` 结束；`finishing` 也可能以 `error`（`llm_unavailable`）结束。`RunStatus` 里的 `queued` / `running` 这个接口实际从不返回。
- `loop.py:330-331` 的 `finishing` 守卫删掉后全部测试仍通过（见变异 M1）：`loop.py:537` 已保证被接受的 Stop 一律以 `cancelled` 收尾，这个守卫只决定“步骤刚结束的那一瞬间”算谁赢。要么加测试，要么在注释里说明它只是语义选择。
- 切走再切回正在生成的会话：界面不知道 run id，无法 Stop，也看不到生成中状态（发消息会等锁）。本 PR 之前也没有这个能力，可以留给后续（会话详情里带上活动 run）。

## 变异检查结果

后端（`tests/test_stop.py`、`test_agent_runtime.py`、`test_booking_model.py`、`test_agent_loop.py`、`test_agent_concurrency.py`、`test_runtime_review.py`）：

| 变异 | 结果 |
|---|---|
| M1 no finishing guard in cancel_run | **存活** |
| M2 no check after the tool thread | **存活** |
| M3 no check before a model request | **存活** |
| M4 no check before a tool | **存活** |
| M5 stopped only by CancelledError | **存活** |
| M6 no conversation check | 杀死（`test_another_user_another_conversation_or_an_unknown_run_cannot_stop`） |
| M7 no user check | 杀死（`test_another_user_another_conversation_or_an_unknown_run_cannot_stop`） |
| M8 cancel the task while the user message is stored | 杀死（`test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step`） |
| M9 no drain | 杀死（`test_stop_between_model_and_tool_runs_no_tool_and_keeps_the_history_well_formed`） |
| M10 emit after terminal allowed | **存活** |
| M11 flag ignored after storing the user message | 杀死（`test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step`） |
| M12 aborted call not logged | 杀死（`test_stop_during_an_endless_model_stream_closes_it_and_keeps_the_released_text`） |
| M13 no steps_task.cancel (flag only) | 杀死（`test_stop_during_an_endless_model_stream_closes_it_and_keeps_the_released_text`） |
| M14 interrupted tool result kept as cancelled rows skipped | 杀死（`test_stop_between_model_and_tool_runs_no_tool_and_keeps_the_history_well_formed`） |
| M15 refund twice on queued Stop | 杀死（`test_stop_before_the_run_task_begins_stores_nothing_and_refunds`） |
| M17 stopped text gets the click hint/template (fallback text) | 杀死（`test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step`） |
| M18 cancelled status dropped | 杀死（`test_stop_while_the_user_message_is_being_stored_keeps_it_and_starts_no_step`） |
| M19 context | **存活** |
| M20 finished run forgotten at once | 杀死（`test_stop_before_the_run_task_begins_stores_nothing_and_refunds`） |
| M21 Stop of a queued run does not cancel the task | 杀死（`test_stop_while_waiting_for_the_conversation_lock_stores_nothing_and_refunds`） |

存活的 6 个（M1–M5、M10）都是“第二道防线”：`steps_task.cancel()` 正常传播时这些检查不会被走到，所以删掉后 233 个测试全过。见 P2-9。

前端（`tests/stop.test.tsx`）：

| 变异 | 结果 |
|---|---|
| F1 流结束后不检查 `here()` | **存活**（P2-6） |
| F2 `detach` 不 abort 且回调不检查 `here()` | 杀死 |
| F3 只删回调里的 `here()` | 存活（abort 之后基本等价） |
| F4 `catch` 里不看 `run.stop` | 存活（等价：后面 `run.stop` 分支先返回） |
| F5 重载后不检查 `here()` | **存活**（P2-6） |
| F6 `detach` 不复位 `stopping` | **存活**（P2-6） |
| F7 grace 定时器不触发 | 杀死 |
| F8 run id 迟到时不补发 cancel | 杀死 |
