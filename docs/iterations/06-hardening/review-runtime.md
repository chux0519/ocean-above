# 评审：06-hardening P0a（#32 / PR #40，3b74189）+ P0b（#33 / PR #42，83f84e7）

只读评审，main @ e3ded27。未改仓库文件，未调用真实 LLM，未读 .env。
复现实验（假 LLM，临时目录）：`/tmp/oa-review-exp/exp.py`，下文标 “实测” 的数字来自它。
路径相对 `backend/app/`。

## 结论

- **没有找到出票、改 quote 状态或绕过确认的路径。** 被拒的工具调用不会执行；预算 / 超时 / 并发停止都不会出票；
  app.db 超时在出票事务里整体回滚，nonce 可重试。
- **2 条 P1**（都是可用性，不是安全）：订票点击会占满共享线程池；失败重试按估算计费，使中长会话里重试等于没有。
- 其余 P2：预算记账的几个洞、拒绝行泛滥、时限覆盖范围、测试缺口。

## P1

### P1-1 订票点击等会话锁时占用共享线程池，可拖停全站所有轮次（CONFIRMED，实测）
- 位置：`airline/service.py:286-290`（`book_async` → `asyncio.to_thread(self.book)`）、`:276`（`locks.hold(timeout=5)` 在线程里阻塞等）；
  `agent/concurrency.py:143`、`agent/loop.py:194,207-215,319`（轮次的所有阻塞调用用同一个默认 executor）。
- 问题：默认 executor 只有 `min(32, cpu+4)` 个线程（小 VPS 上 5–6 个），没有单独配置。每个等锁的点击占一个线程最多 5 s；
  `/quotes/{id}/book` 没有限速，也不消耗额度，nonce 在拿到锁之后才校验。P0b 让锁被持有得更久（排名额 5 s、drain 30 s），
  §4.9 的 “线程占着名额，因此线程数有上限” 没算上这条路径。
- 场景：用户在会话 X 发一条消息（轮次持锁最多 90 s），同时对 X 里任一自己的 quote 并发 POST book（nonce 随便填），
  数量 ≥ executor 线程数，每 5 s 重发一批。实测：14 个线程被 14 个点击占满，别的 `to_thread` 等了 4.81 s。
  期间所有用户的工具调用、`add_message`、额度预留 / 退还都排在后面，轮次整体超时。事件循环本身不阻塞，但没有线程可用。
- 建议：订票等锁改成 `hold_async`（事件循环上等，拿到锁后再 `to_thread` 做事务）；或给订票单独的小线程池 / 信号量；
  再给 book 加每用户并发上限。并显式设置默认 executor 大小 ≥ `AGENT_MAX_ACTIVE_TURNS` + 余量。
- 说明：`book_async` 是 03 的代码，不是这两个 PR 引入的，但它直接破坏 #33 要保证的东西。

### P1-2 失败的尝试按 “请求字符 / 2 + 4096” 计费，中长会话里一次瞬时错误就把本轮判成 token_budget（CONFIRMED，实测计费）
- 位置：`agent/loop.py:350-358,384-389`（失败分支 `log_call(...)` 不带 usage）、`agent/budget.py:121-130,84-87`。
- 问题：HTTP 非 200、连接失败这类请求没有消耗任何 token，却按最保守估算计入。默认上限 20 000。
- 场景：上下文约 27.6k 字符（12 轮较长消息；历史预算本来允许 40k–80k 字符）。第一次尝试 503 → 实测计 17 882。
  重试成功并返回工具调用，真实用量约 7–9k → 累计超过 20 000 → 工具执行后 `stop_before_step` 拒绝下一步 →
  模板回复（`token_budget`）。上下文 ≥ 约 32k 字符时，单次失败本身就超过上限。结果：重试成功了，用户仍拿到模板。
- 建议：没读到任何响应体的失败（HTTP 状态错误、连接 / 握手错误）计 0；只对流中途断开的调用用估算，
  且估算输出用已收到的字符数而不是整个 4096。加一个默认预算 + 真实大小上下文的测试。

## P2

### P2-1 `usage.total_tokens == 0` 被当作有效回报（CONFIRMED，实测）
- 位置：`agent/budget.py:93`（只拒绝 `< 0`）。
- 场景：服务商 / 代理在 usage 块里回 0（或只填 prompt/completion、total 给 0）→ 每次计 0，本轮 token 上限永不触发（实测 5 次调用后 tokens=0）。
- 建议：`total <= 0` 视为缺失走估算；或取 `max(total, prompt_tokens + completion_tokens)`。

### P2-2 token 上限在重试之间不检查，实际超出可达 3 次调用而不是 “一整步”（CONFIRMED，读代码）
- 位置：`agent/loop.py:343-390`（重试循环内不问 `budget`），设计 §4.8 写 “单次调用最多可以超出剩余额度一整步”。
- 场景：流中途断开（已产生真实 token、未放出文本）→ 重试 2 次，每次都是完整请求；只有回到 `_steps` 才检查。
- 建议：重试前检查 `tokens >= max_total_tokens_per_turn`，超了就不再重试；或改文档为 “最多 retries+1 次调用”。

### P2-3 一次 completion 里的大量工具调用：每个被拒调用都落一行并进上下文（CONFIRMED，实测）
- 位置：`agent/loop.py:269-289`。
- 场景：一次 completion 带 300 个调用（4096 输出 token 内可以做到）：4 个执行，296 个被拒，实测写了 300 行 `tool_calls`，
  本轮下一次请求 115 729 字符（约 3 万 token，发出去之前 token 上限无从得知）；下一轮整轮因超过 80k 硬上限被丢成摘要，
  连带这一轮的正常结果也从上下文消失。不会多执行任何工具。
- 建议：completion 的调用数超过某个上限（例如 `max_tool_calls_per_step × 4`）时，整步按 `tool_budget` 结束本轮，
  不逐条落拒绝；或拒绝内容缩成最短并在首条之后不再附说明文字。

### P2-4 被整轮时限打断的 LLM 调用既不记账也不落 `llm_calls`（CONFIRMED，读代码）
- 位置：`agent/loop.py:384`（只接 `LLMError` / 内层 `TimeoutError`；外层时限注入的是 `CancelledError`）。
- 场景：第 2 步流了 50 s 时 90 s 到点 → 该调用无记录，全站每日 token 预算也看不到它。#32 要求 “保留已观察用量的日志，包括失败”。
- 建议：`except asyncio.CancelledError` 里用 `turn.threads.run` 落一条 `{"aborted": "turn_timeout"}`（估算计费）再 `raise`；drain 会等它写完。

### P2-5 时限落在 `add_tool_call` 上时：行已写入，但卡片和 outcome 丢失（PLAUSIBLE，读代码）
- 位置：`agent/loop.py:273-288`（`turn.outcomes.append` / `emit("card")` 在两个 await 之后）。
- 场景：`quote_route` 已建 quote，`add_tool_call` 线程在 drain 中写完，但取消发生在 await 上 → 模板回复不含这条 quote，
  用户没看到卡片；下一轮上下文和 guard 记录里却有它，LLM 可以在文字里报它的价格。quote 仍是 `quoted`，无法出票（B2 不受影响），
  之后 `request_booking` 仍会给出完整确认卡。
- 建议：工具返回后先同步登记 outcome 和卡片，再 await 落库；或 drain 后从已完成的 future 取回 outcome。

### P2-6 默认 20 000 对长会话偏紧（PLAUSIBLE，未跑真模型）
- 位置：`agent/budget.py:44` 对比 `agent/context.py:26-27`（历史 40k 字符全量、80k 硬上限）。
- 场景：历史接近 40k 字符时单步约 1.2 万 token 以上；需要 3 步的轮次（`quote_route` → `request_booking` → 文字，
  即 `book_in_one_message`）在第 2 步后累计超过 20k → 模板。接近 80k 时任何用工具的轮次都只跑一步。行为安全，但回复全是模板。
- 建议：用 #34 的 eval 数据核一下长会话的每步 token；上限按 “上下文上限 × 预期步数” 定，或把历史预算调小到与之匹配。

### P2-7 app.db 写锁期间读 locations.db，没有时限（PLAUSIBLE，读代码）
- 位置：`airline/service.py:309`（`_recheck` 在 `BEGIN IMMEDIATE` 内）、`:184`（`_similar` 同样）；`store/db.py:247-249`。
- 问题：操作时限靠 progress handler，只在 SQL 执行中每 1000 条 VM 指令检查；Python 代码持有写事务时不计时，短语句也不会触发。
- 场景：`_recheck` 重新加载 locations.db 卡住（文件系统慢）→ 写锁一直被占 → 其他所有写入 10 s 后 `StoreUnavailable`，
  全站轮次走 `internal_error`，直到它返回。§4.9 的 “未覆盖” 提了 locations.db 无时限，但没提它发生在写锁里。
- 建议：事务外先算好复核结果和指纹，事务内只比对指纹再写；或在文档里写明这个后果。

### P2-8 名额没有每用户上限（PLAUSIBLE）
- 位置：`agent/concurrency.py:93-119`、`agent/loop.py:184-185`。
- 场景：一个用户开 8 个会话同时发消息，占满全部 8 个名额，其他人 5 s 后 `server_busy`。受每日 50 条限制，
  单账号每天最多占用几分钟；`server_busy` 的请求全额退还，所以被挤掉的人不吃亏，攻击者被拒的请求也不花额度。
- 建议：每用户同时运行的轮次上限（例如 2），超出按 `conversation_busy` 类错误拒绝。

### P2-9 小问题
- `agent/loop.py:150`：`stream_turn` 在事件循环上同步读 app.db（03 遗留）；路由在前一步已用 `to_thread` 查过同一件事，可删掉或挪进任务。
- `agent/loop.py:184-185`：排名额时已持有会话锁，全站满载时同会话的订票点击会因此得到 `conversation_busy`（最多多等 5 s）。
- `agent/loop.py:198-200,208`：`add_message` 遇 `StoreUnavailable` → `internal_error` 文案说 “Your message was saved”，实际没存，额度也没退。
- `api/errors.py:54-58`：app.db 超时的 503 文案是 “Route data could not be read”，与原因不符。
- 全站每日 token 预算仍只累计服务商回报值；服务商不回 usage 时它不起作用，而 P0a 的估算值就在 `response.budget` 里可用。

## 检查过、没有问题的点
- **被拒调用不执行**：`admit_tool_call` 是唯一入口，拒绝分支不碰 `Toolbox`；每个 `tool_call_id` 都有结果，落库行数与 completion 对齐，`build_context` 能重建。
- **停止不出票**：agent 只持有 4 个工具和 `post_assistant_message`；超时 / 预算 / LLM 错误后已推送的卡片照常 `presented`，出票仍只能由点击触发。
- **不良构会话**：时限打断在一步中间时，`context.steps()` 行数对不上会退回 “每个调用一条 assistant 消息”，协议仍有效。
- **TurnSlots**：FIFO 交接、超时与交接同时发生（保留名额）、等待者被取消（归还名额）都没有泄漏；锁 → 名额顺序固定，订票只拿锁，没有环。
- **server_busy 退还**：只在 `_run` 里调用一次，按预留 id 删除，重复调用和跨 UTC 日都无副作用；客户端断开也会执行。
- **客户端断开**：轮次在自己的任务里跑完并落库，锁和名额在 drain 之后释放。
- **app.db 超时**：`write()` 遇 “locked / interrupted” 整体回滚（含 SQLite 自动回滚的情况），事务内没有吞掉 sqlite 异常的代码；
  出票 503 后 quote 仍 `presented`，同一 nonce 可再用。

## 测试能否抓到这些问题
能抓到：超额调用不执行（含变异测试）、协议良构、token 上限在步间生效、名额模型测试、drain 先于落库和解锁、锁等待 503 后 nonce 可重试。

缺口：
1. 出票事务只测了 `BEGIN IMMEDIATE` 等锁失败（什么都没写之前）；没有 “写了 `mark_nonce_used` 之后被中断” 的回滚测试，抓不到部分写入。
2. 重试 / 估算的测试都用很小的预算和很短的上下文，测不出 P1-2。
3. 没有 executor 饱和的测试（P1-1）；事件循环响应性测试只量循环延迟，不量 `to_thread` 排队。
4. 时限打断后没有断言下一轮 `build_context` 良构，也没覆盖打断落在 `add_tool_call` 上（P2-5）。
5. drain 超时的测试只断言结尾是 `done` 或 `error`，过宽。
6. 没有 “客户端断开 + server_busy 仍退还” 的测试；HTTP 的 server_busy 测试是直接改 `slots.active`，不是真实并发。
7. 没有 `total_tokens: 0`、单次 completion 上百个调用的用例。
