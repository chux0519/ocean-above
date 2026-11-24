# PR #56 评审：opt-in OpenTelemetry tracing（#35）

评审对象：分支 `it10-otel`，HEAD `a785642`，对比 `origin/main...HEAD`。只读评审，未改仓库文件。
依赖版本：pydantic-ai-slim 2.71.0、opentelemetry-sdk 1.45.2、requests 2.34.2。

## 结论

- **没有 P0。** 没找到 tracing 改变对话、出票、锁或名额的路径。
- **P1 一条：** 环境里有 `HTTP_PROXY` / `ALL_PROXY` 时，发往“本地”端点的 span 实际走代理，且没有告警。
- **P2 九条：** 关停 flush 上界、`_trace_end` 的位置、测试缺口、文档与实现不符等。
- 全量测试 927 通过（`HYPOTHESIS_PROFILE=ci`），`tool/review_lint.py` 通过。
- 没跑的：`docker compose config`（本机无 docker）、真实模型 evals、Phoenix 实机。

## P1

### P1-1 有 HTTP 代理时，span 走代理离开本机，`endpoint_is_local` 不告警 — CONFIRMED
- 位置：`backend/app/tracing.py:93`（`OTLPSpanExporter(endpoint=..., timeout=...)`，没传 `session`）；`.env.example:59`。
- 触发：进程环境有 `HTTP_PROXY` 或 `ALL_PROXY`，`NO_PROXY` 不含端点主机。本机 `proxy_on` 之后就是这个状态。
- 实测（`/tmp/oa-review56/probe_c.py`，假代理记录请求行）：

  | 代理变量 | `http://phoenix:6006` | `http://localhost:4318` | `http://127.0.0.1:4318` |
  |---|---|---|---|
  | `HTTP_PROXY` | 走代理 | 走代理 | 走代理 |
  | `ALL_PROXY` | 走代理 | 走代理 | 走代理 |
  | 只有 `HTTPS_PROXY` | 直连 | 直连 | 直连 |

- 后果：
  - 与 plan 的“只导出到配置的本地端点”不符。span 没有正文，但有 run id、message id、会话哈希、LLM 主机名、token 数。
  - tracing 静默失效：远端代理连不到它自己的 `localhost`，span 全丢。
- `.env.example:59` 的注释写的是 `HTTPS_PROXY`，但 `http://` 端点看的是 `HTTP_PROXY` / `ALL_PROXY`。compose 部署只设 `HTTPS_PROXY`，所以不受影响；受影响的是宿主机直接跑后端。
- 建议：端点判定为本地时传 `session=requests.Session()` 且 `session.trust_env = False`；注释改成正确的变量名；加一个“设了 `HTTP_PROXY` 仍直连”的测试。

## P2

### P2-1 collector 挂起时关停要等 20 s，不是“一个导出超时” — CONFIRMED
- 位置：`backend/app/tracing.py:162-170`（`shutdown`），`backend/app/api/routes.py:385-386`。
- 触发：collector 接受连接但不应答，队列里有多于一批的 span。
- 实测（`probe_b.py`）：

  | 队列里的 span | `tracing.shutdown()` 耗时 |
  |---|---|
  | 一批以内 | 5.0 s |
  | 2000 个（4 批 × 512） | 20.0 s |

- 请求路径不受影响：同样条件下一轮对话加出票 0.23 s。
- 后果：应用关停被拖到 20 s 以上，超过 `docker stop` 默认的 10 s，会被 SIGKILL。数据无损，但 STATE 和 design 草稿写的“最多等一个导出超时”不对。
- 现有测试只测了端口关闭（立刻被拒绝）的情况。
- 建议：先 `force_flush(timeout_millis=…)` 给总上界，再在 daemon 线程里 `shutdown` 并限时 `join`；或者把文档改成实际上界。

### P2-2 `_trace_end` 在 `finally` 里排在 `_retire` 和 `queue.put_nowait(None)` 之前 — CONFIRMED（危险形态），触发概率低
- 位置：`backend/app/agent/loop.py:482-484`。
- 现状是安全的：`_Span` 吞掉所有 `Exception`，`budget.snapshot()` 是纯字典。探针 A 让真实 span 的 `set_attribute(s)` / `add_event` / `end` / `record_exception` / `set_status` 全部抛错，对话、错 nonce 拒绝、`book_async` 出票、LLM 失败、guard 回退的结果与关闭时一致，`_runs` 清空。
- 但这一行只要抛错，流就永远不结束，run 也不退场。变异 M17（让这一行在 tracing 打开时抛错）使测试直接挂死。
- 建议：改成 `try: _trace_end(...) except Exception: log.debug(...)`，或者先取 `turn = run.turn`，把 `_trace_end` 挪到 `queue.put_nowait(None)` 之后。
- 同类问题：属性值的计算都在保护之外，只有 `set_attribute` 本身被保护。
  - `service.py:138-139`：`result.data.get("legs")` 在 quote 已落库之后执行。
  - `service.py:452-454`：`_reason_kind(problem)` 在出票事务里执行。
  - `loop.py:806`：`{p["kind"] for p in problems}`。
  - `tracing.py:274`：`live.is_recording()` 在 `try` 之外，由 `loop.py:537`（用户消息已落库、`run.turn` 未设）调用。
  - 今天都不会抛，但以后加属性的人容易踩。建议统一成属性计算也进保护。

### P2-3 测试抓不到“tracing 层自身抛错”的大部分情况 — CONFIRMED（变异检查）
- 位置：`backend/tests/test_tracing.py:390`（`test_a_broken_tracing_layer_never_reaches_the_turn` 只破坏 `get_tracer`）。
- 在 `/tmp/oa-review56/mut` 的副本上做变异，只跑 `tests/test_tracing.py`：

  | 变异 | 结果 |
  |---|---|
  | M1 去掉 `_Span.set_attribute` 的 try | 存活 |
  | M2 去掉 `_exit` 的 try | 存活 |
  | M18 去掉 `_Span.add_event` 的 try | 存活 |
  | M6 `Redacting` 不清 status 描述 | 存活 |
  | M10 `span()` 只接 `Exception`（`CancelledError` 时 span 不结束、context 不还原） | 存活 |
  | M15 `book_async` 去掉 `_book_status` | 存活 |
  | M3 `include_content=True` | 被杀（4 个测试失败） |
  | M13 保留异常事件的消息 | 被杀 |

- M6 存活的原因：测试里的异常只经过 SDK 的 span，而 SDK 在 `include_content=False` 时自己不写描述。经过我们自己 span 的异常没被测到，例如 `NotFound(conversation_id)` 穿过 `airline.quote_route`、带路径的 `NetworkDataError` 穿过 `booking.recheck`。实现本身是对的：探针 E 里自有 span 的异常导出为 `ERROR`、描述 `None`、事件只有 `exception.type`。
- 建议：把探针 A（`/tmp/oa-review56/probe_a.py`）改成测试；加一个异常穿过自有 span 的脱敏测试；加一个经 HTTP 出票后 `ocean.book.status` 的断言。

### P2-4 模型生成的工具名原样导出 — CONFIRMED
- 位置：SDK 的 `chat` span（`gen_ai.output.messages`）；我们的 `Redacting` 不处理它。
- 触发：模型调用一个不存在的工具。探针 D 里 `no_such_tool` 出现在导出内容中。SDK 明确说工具名不受 `include_content` 控制。
- 后果：工具名是模型输出，可能被提示注入带出任意短字符串，与“不含模型文字”的表述有出入。
- 探针 D 的其余流程 0 命中：未知城市、坏 JSON 参数、错类型参数、thinking、回显用户文字的 LLM 错误、12 个并发调用、`list_tickets`、guard 失败。
- 建议：在 `Redacting` 里把不在工具表里的名字换成 `unknown_tool`；或者在文档里写明。

### P2-5 会话哈希没加盐 — CONFIRMED（设计说明缺失）
- 位置：`backend/app/tracing.py:282`（`sha256(id)[:16]`），用在 `session.id`、`ocean.conversation_hash`、`gen_ai.conversation.id`。
- 会话 id 是 `c_` 加 64 位随机数，无法从哈希穷举反推。但知道某个会话 id 的人可以确认哪条 trace 属于它，有 app.db 的人可以整表对应。
- 建议：改成用服务端密钥的 HMAC；或者在 design §4.11 写明“可关联、不可反推，只面向本机运维”。

### P2-6 `locations.db` 读取时限的说明比实际强 — CONFIRMED
- 位置：`backend/app/core/network.py:193-210`，STATE 第 25 行，design 草稿。
- 异常映射正确：进度回调中断 → `sqlite3.OperationalError("interrupted")` → `NetworkDataError`。
- 出票复核里超时不会把 quote 标成 stale。探针 F：复核期间读超时，连点两次都是 `data_unavailable`，quote 仍是 `presented`，nonce 未消耗；恢复后同一次点击出票成功，票数 1。`NetworkProvider` 失败后清掉缓存，下次重新读，不会卡死在错误上。
- 不准确的三点：
  - `sqlite3.connect` 的默认 `timeout` 本来就是 5 s，等锁的行为没变。STATE 写的“以前无限等待”只对 SQL 执行成立。
  - 进度回调只在 SQLite 虚拟机执行时触发，文件系统卡住（比如外置卷）中断不了。`_sha256`、`_stat`、`_refuse_wal` 也不在时限内。测试名里的 “not a hang” 说过头了。
  - `load_network` 最多循环 3 次，全程持有 `NetworkProvider._lock`；出票时还持有 app.db 写事务。最坏情况远大于 5 s，但比以前只好不差。
- 建议：改文档措辞；要真正的上界得把整个加载放到带超时的线程里，可以留给后续。

### P2-7 出票请求被取消时，`airline.book` span 没有状态 — CONFIRMED（读代码）
- 位置：`backend/app/airline/service.py:308-322`、`514-522`。
- 触发：`book_async` 的调用方被取消。`_to_thread_to_the_end` 仍会等事务提交，然后 `CancelledError` 穿过 `_book_status`，它只处理 `BookingError`。
- 后果：票已出，span 上却没有 `ocean.book.status`。只影响观测。
- 出票行为没变：结果、异常类型、B1–B7 的顺序、会话锁、`_booking` 计数的 `finally` 都在 span 里面，原样保留；`_book_status` 对所有异常都原样重抛。
- 建议：在 `_book_status` 里对其他 `BaseException` 记 `interrupted`。

### P2-8 PR 说明写“没有 id”，实际导出了几种 id — CONFIRMED
- 位置：PR 正文 Privacy 一条；STATE 的 span 清单是准确的。
- 实际导出：`ocean.run_id`、`ocean.message_id`（app.db 消息 id）、`gen_ai.response.id`（供应商的 completion id）、`gen_ai.tool.call.id`、`gen_ai.agent.call.id`、`server.address`（LLM 主机名）。
- 都不是用户内容，但 `message_id` 能直接对到 app.db 的一行。
- 建议：合并 design §4.11 时列全这几项。

### P2-9 Phoenix 镜像自身的遥测 — PLAUSIBLE（未验证）
- 位置：`docker-compose.yml` 的 `phoenix` 服务。
- 印象中 Phoenix 默认开启匿名使用统计，可用 `PHOENIX_TELEMETRY_ENABLED=false` 关闭。我没查 22.0.1 的文档。
- 建议：协调者在 homelab 跑 `docker compose --profile tracing config` 时一并确认，需要的话在 compose 里关掉。

## 逐项核对（7 个重点）

1. **`_run` / `_run_traced` 与 `_trace_end`**
   - 正常：`_run_traced` 的函数体与原 `_run` 一致，锁 → 用户名额 → 全站名额的顺序和各 `except` 分支没动。
   - 正常：tracing 调用抛错不会打断 `_retire`、`queue.put_nowait(None)`、终态事件、锁和名额释放（探针 A）。
   - 正常：`CancelledError` 穿过 `with tracing.span` 时原样传播，span 结束，context 还原（探针 E）。
   - 问题：P2-2。
2. **`book` / `book_async`**：行为不变，见 P2-7 的说明。
3. **`quote_route` / `_recheck` 包装**：返回值和异常不变；属性计算在保护之外（P2-2 同类）。`_reason_kind` 的所有 problem 字符串冒号前都是固定文字，不含城市名。
4. **读取时限**：映射正确，不会永久 stale（探针 F）；说明过强（P2-6）。
5. **隐私**
   - 正常：`include_content=False` 覆盖 `chat` 和 `execute_tool` span，消息只剩结构，工具参数和结果不出现（探针 D，变异 M3 被杀）。
   - 正常：SDK 的 `conversation_id` 参数只进 span 属性和 OTel baggage。没有 HTTP 插桩，不会上线路；关闭 tracing 时只是盖在 SDK 的消息对象上，我们不存这些对象。
   - 问题：P1-1、P2-4、P2-5、P2-8。
6. **开销**
   - 关闭时：现有的新进程测试确认不导入 SDK、无线程。唯一多出来的是每轮 3 次 `sha256`（`loop.py:430-431`、`623`），可忽略。
   - 打开时：exporter 宕机不阻塞请求路径（探针 B）；关停受影响（P2-1）。
7. **测试**：见 P2-3。

## 探针与变异脚本

都在 `/tmp/oa-review56/`：`probe_a.py`（span 方法全抛错）、`probe_b.py`（挂起的 collector）、`probe_c.py`（代理）、`probe_d.py`（隐私边界流程）、`probe_e.py`（自有 span 脱敏与取消）、`probe_f.py`（复核期间读超时）、`mutate.py`（变异，作用于 `/tmp/oa-review56/mut` 副本）。

运行方式：`cd backend; uv run python /tmp/oa-review56/probe_X.py`。

副作用：`uv run` 在本 worktree 建了 `backend/.venv`（未跟踪）。
