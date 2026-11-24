# POC：Pydantic AI Slim `[openai]` 作为 agent 运行时（#49 阶段 1，P-PA）

日期 2026-11-24。分支 `it08-poc-pa`。代码在 `spikes/pydantic-ai/`（独立 uv 项目，自己的 `pyproject.toml` + `uv.lock`；
`sys.path` 引入 `../../backend`，直接复用 `Toolbox`、`TOOL_SCHEMAS`、`Airline`、`ReplyGuard`、`BudgetTracker`、`Threads`、
`build_context`、`system_prompt`）。`backend/` 一行没改。

| 项 | 值 |
|---|---|
| 版本 | `pydantic-ai-slim` 2.71.0（`[openai]` → `openai` 3.58.0，走 `httpx2` 2.16.2），Python 3.12.8 |
| 模型 | DeepSeek `deepseek-flash`（V4.1 Flash），`OpenAIChatModel` + `DeepSeekProvider(api_key, base_url)`，key 由 `LLMConfig.from_env()` 在进程内读 `OCEAN_ENV_FILE` |
| 设置 | 同 `config.py` / `llm.py`：`temperature=0`、`max_tokens=4096`、thinking `high` |
| 脚本 | `poc/checks_fake.py`（本地假 OpenAI 兼容服务器 `poc/fake_server.py`，零花费），`poc/checks_real.py`（真 DeepSeek），`measure.sh` |
| 原始输出 | `results/checks_fake.{json,log}`、`results/checks_real.{json,log}`、`results/checks_real_7.json`、`results/checks_real_latency10.{json,log}`、`results/measure.txt` |
| 真模型花费 | 结果文件里记录的 $0.0148；加上未记录的调试运行和第一轮全量运行，合计约 **$0.03**（上限 $0.04，预算 $0.15） |

接口先查源码再用（不是凭记忆）：`Agent.iter(..., deps, message_history, model_settings, usage_limits,
cancellation_token, timeout)`（`agent/__init__.py:1491`）、`AgentRun.cancel()`（`run.py:911`）、`CancellationToken`
（`_cancel.py`）、`UsageLimits`（`usage.py:546`）、`ModelSettings.thinking / request_timeout / first_event_timeout /
max_retries`（`settings.py`）、`Tool.from_schema`（`tools.py:526`）、`ToolManager.parallel_execution_mode`
（`tool_manager.py:400`）、`DeepSeekProvider.model_profile`（`providers/deepseek.py`，DeepSeek 专门的 profile：
`reasoning_content` 字段、工具调用必须回传 reasoning、`max_tokens` 而不是 `max_completion_tokens`）。
注意 2.71 已经没有 `Agent(instrument=...)`，改为 `capabilities=[Instrumentation(...)]`；`run.usage()` 改成属性。

## 接法（迁移后也这样用）

- 四个工具：`Tool.from_schema(fn, name, description, json_schema=TOOL_SCHEMAS[...]["parameters"], takes_ctx=True)`，
  `fn(ctx, **kwargs)` → `Toolbox.run(ctx.deps.user_id, ctx.deps.conversation_id, name, json.dumps(kwargs))`。
  校验、结构化错误、`Outcome` 都还是我们的代码（`from_schema` 跳过 SDK 的参数校验）。
- 驱动：`async with agent.iter(...) as run: async for node in run:`，`ModelRequestNode` / `CallToolsNode` 各自
  `async with node.stream(run.ctx)` 取事件（`PartStartEvent` / `PartDeltaEvent(TextPartDelta|ToolCallPartDelta)` /
  `FunctionToolCallEvent` / `FunctionToolResultEvent`），在这里做 SentenceBuffer + ReplyGuard + SSE 映射。
- 串行执行工具：`with ToolManager.parallel_execution_mode("sequential")`（默认是并行）。

## 逐条验证

### 1 流式文本 ✅
真 DeepSeek，生产 system prompt，`PROMPT_1`，每次回复 32–61 个 `TextPartDelta`。首个可见文字（从 `iter` 开始计）
与现有 `llm.py` 客户端同一请求交替测 10 次（`results/checks_real_latency10.json`）：

| | 中位数 | 10 次 |
|---|---|---|
| Pydantic AI | **1.031 s** | 1.457 0.622 1.354 1.286 0.985 0.795 0.979 1.066 1.222 0.996 |
| 现有 httpx 客户端 | **1.220 s** | 0.816 1.471 1.230 1.209 0.815 0.697 0.743 1.371 1.247 1.338 |

两者区间重叠，差别是网络噪声；SDK 没有可测的额外延迟。thinking 文本以 `ThinkingPart` 事件先到（0.68–1.08 s）。

### 2 一次完成里多个工具调用、多步串行 ✅
真 DeepSeek（`results/checks_real.json` → `2_tools`）：
```
parallel: [["find_city({\"name\":\"Abu Dhabi\"})", "find_city({\"name\":\"Reykjavik\"})"], []]  codes [no_service, ok]
serial:   [["quote_route({\"origin\":\"Paris\",\"destination\":\"Sydney\"})"],
           ["request_booking({\"quote_id\":\"q_…\"})"], []]                                   codes [ok, ok]
```
注意：SDK 默认**并行**执行一次完成里的多个工具（假服务器：`slow_then_more` 并行时 `find_city` 在慢工具之前就跑完了）。
我们的预算按顺序逐个准入，必须 `parallel_execution_mode("sequential")` 或每个工具 `sequential=True`。
SDK 还会自动给工具 schema 加 `"strict": true`（三个有必填参数的工具；`list_my_tickets` 没加），DeepSeek 接受了；
要保持现在的线上格式，迁移时把 `tool.strict = False`。

### 3 工具抛错 / 参数非法 / ModelRetry ✅（语义有差别）
假服务器（`checks_fake.json` → `3_8_tool_errors`）：

| 情况 | 结果 |
|---|---|
| 合法 JSON、未知参数（`{"city":"Paris"}` 给 `find_city`） | 进我们的 `Toolbox.run` → `invalid_arguments` 结构化结果，run 继续（2 次请求）——与现在相同 |
| 未知工具名 `book_ticket` | SDK 回一条 retry 提示给模型，run 继续（2 次请求） |
| 工具 `raise RuntimeError` | **整个 run 抛 `RuntimeError`**（不转成结果）。我们的 `Toolbox.run` 从不抛，所以不受影响 |
| 工具 `raise ModelRetry` | 回 retry 提示，模型重试后成功（3 次请求） |

### 4 运行中取消 ✅
假服务器无限流，首个文字后 0.3 s 取消，每种 7 次（`4_6_cancel`）。"上游关闭"= 假服务器在自己线程里看到客户端
关闭连接（EOF）的时刻减去发出取消的时刻：

| 方式 | 结果 | 上游关闭中位数 | 7 次都关闭 | 之后的 LLM 请求 |
|---|---|---|---|---|
| `run.cancel()`（另一个 task） | `RunCancelled`，保留已完成的消息 | 1.8 ms | 是 | 0 |
| `CancellationToken.cancel()`（另一个线程） | `RunCancelled` | 1.6 ms | 是 | 0 |
| `asyncio.Task.cancel()` | `CancelledError` | 1.6 ms | 是 | 0 |
| 消费者 `break` 出流并离开 `iter`（ReplyGuard 失败的路径） | 正常离开 | 1.8 ms | 是 | 0 |
| `run_stream()` + `StreamedRunResult.cancel()` | 上游关闭 2.8 ms | — | 是 | 0 |

`run_stream` + `result.cancel()` 会留下一条 "Task exception was never retrieved: ModelConnectionError"（日志噪声）；
用 `iter` + `run.cancel()` 没有。真 DeepSeek（HTTPS 经代理，`4_6_9_cancel_real`，3 次）：`run.cancel()` 后
**0.6 ms** 响应流 `aclose()`，`RunCancelled` 3.0–3.3 ms 后抛出，流没有读到 EOF，HTTP 请求只有 1 个。

### 5 同步工具在线程里执行中被取消 ⚠️ 需要适配层
慢工具 `time.sleep(1.0)`，进入后 0.3 s 取消（`5_cancel_in_sync_tool`）：
```
single_run_cancel   RunCancelled  returned 2 ms   thread ended 695 ms after cancel  (returned before thread ended)
two_parallel        RunCancelled  returned 2 ms   both threads ended ~700 ms later
slow_then_find_city sequential    RunCancelled    find_city never ran;  parallel: find_city already ran
```
SDK 取消后**不等**正在跑的线程（`cancel_and_drain` 用原生 `Task.cancel()`，穿过了 anyio 的 shield；
`_utils.run_in_executor` 的文档说默认 shield，实测不是）。线程会跑完，但 run 已经返回——如果这是出票前的
`quote` 写入，就会在锁释放后才写。所以必须保留现有的 `Threads`：工具写成 async，阻塞部分走
`await threads.run(...)`，取消后 `await threads.drain(30)` 再落库、放锁：
```
adapter_Threads_then_drain             returned 701 ms  thread ended 700 ms  (lock released after the thread)
adapter_Threads_then_drain_sequential  returned 698 ms  thread ended 698 ms  find_city never ran
```
串行模式下，取消后后面的工具不会开始。

### 6 取消后上游 HTTP 流真的关闭，不再有新请求和工具调用 ✅
见 4：假服务器在服务端看到连接关闭（1.6–1.8 ms），真 DeepSeek 看到响应流 `aclose()`（0.6 ms）；所有方式
`llm_requests_total = 1`，取消后再等 0.5–2 s 也没有新请求；串行模式下取消后没有新的工具调用（5）。

### 7 DeepSeek thinking 参数和 reasoning_content ✅
真 DeepSeek（`checks_real.json` → `7_thinking`）。`ModelSettings(thinking=...)` 在 DeepSeek 上的请求字段：

| 设置 | 发出的字段 | reasoning tokens |
|---|---|---|
| `thinking="high"` | `reasoning_effort: "high"`（**没有** `thinking: {type: enabled}`） | 142，`ThinkingPart` 549 字符 |
| `thinking=False` | `reasoning_effort: "none"` | 无 |
| `thinking="max"` | `reasoning_effort: "max"` | 209 |
| `extra_body={"thinking":{"type":"enabled"},"reasoning_effort":"high"}`（现在 `llm.py` 的形式） | 原样发出 | 133 |

带工具的对话里，后续请求的 assistant tool-call 消息带回了 `reasoning_content`（profile 的
`openai_chat_send_back_thinking_parts='field'`），DeepSeek 没报 400。简单问题 DeepSeek 自己决定不思考
（reasoning tokens = 0），这是模型行为，与 SDK 无关（现有客户端同样）。

### 8 增量 tool-call 拼装、畸形参数 ✅（语义有差别）
假服务器把参数切成 4–5 段：`PartDeltaEvent(ToolCallPartDelta(args_delta='{"na'))…` → `PartEndEvent` 时拼成
`{"name": "Paris"}`，两个并列调用按 index 分开。畸形 JSON `{"name": "Par`：SDK 不调用工具，回模型一条
tool 消息 `{"error":[{"type":"json_invalid",…}]}`，模型改正后继续（2 次请求）。**模型一直给畸形参数时**，超过每个
工具的重试次数（默认 1）后整个 run 抛 `UnexpectedModelBehavior: Tool 'find_city' exceeded max retries count of 1`
（`retries=3` 时 4 次请求后抛）。现在的 loop 是把它记成 `invalid_arguments` 结果，由预算结束本轮、出模板。
迁移时要把这个异常映射成模板回复，或把重试次数设成和每轮工具预算一致。

### 9 usage（正常、取消时）与 UsageLimits ⚠️
- 正常：`RunUsage(input_tokens, output_tokens, requests, tool_calls, cost)`，每个 `ModelResponse.usage` 单独可得；
  真 DeepSeek 的 `details` 带 `prompt_cache_hit_tokens / prompt_cache_miss_tokens / reasoning_tokens`；
  `cost` 来自本地 `genai-prices` 快照（DeepSeek V4.1 Flash $0.30 / $1.20 每百万）。
- **取消时**：`RunCancelled.usage = RunUsage(requests=1)`，token 为 0（DeepSeek 只在最后一个 chunk 发 usage）。
  和现在一样有缺口，仍要用 `budget.charge(..., output_chars)` 估算。
- `UsageLimits`（假服务器）：`request_limit=3` → 第 4 次请求前抛 `UsageLimitExceeded`；`tool_calls_limit=1` 遇到
  一次 2 个调用 → **一个都不执行**，整轮抛；`total_tokens_limit=250` → 在响应**之后**检查，超了就抛，这一步的工具不跑。
  都是"抛异常结束"，不是我们的"多出的调用拒绝、本步继续 / 跑完当前步再停"。

### 10 重试与超时、错误传播 ✅（有一处要补）
假服务器，流式路径（`10_errors_retries_timeouts`）：

| 情况 | 结果 |
|---|---|
| 2 次 500 后 200 | openai SDK 默认 2 次重试，第 3 次成功（1.48 s） |
| 500，`max_retries=0` | `ModelServerError (status_code=500)`，1 次请求 |
| 400 | `ModelRequestRejectedError (status_code=400)`，不重试 |
| 首个 chunk 后不再发，`first_event_timeout=1` | 1.01 s `ModelRequestTimeout`（role chunk 不算输出） |
| 完全不发，`first_event_timeout=1` / `request_timeout=1` | 1.01 s `ModelRequestTimeout` |
| 整轮 `timeout=1`，无限流 | `RunTimedOut`，上游已关闭 |
| **流在没有 `finish_reason` / `[DONE]` 时结束** | **当作正常完成**：`ModelResponse.finish_reason == 'stop'`，只有 `provider_details['finish_reason'] is None` 能看出来 |

最后一条现在的 `llm.py` 当错误（`LLM stream ended without a finish_reason`，可重试）；迁移时适配层要检查
`provider_details`。流中途断开不会被 openai SDK 重试（它只在拿到响应前重试）；`RetryOnModelErrors` capability
能否只在"还没放出文字"时重试没有验证。

### 11 tracing ✅
- 默认没有任何外发：`sys.addaudithook` 记录全部 `socket.connect`：假服务器全程只有 `127.0.0.1`，真模型全程只有本机代理
  （去 DeepSeek）。没有 logfire、没有价格数据下载（`genai-prices` 用包内快照），`tiktoken` 没被触发。
- 唯一的"外发"是启动时往 stdout 打一个推广 Logfire 的 banner；设 `PYDANTIC_AI_NO_BANNER=1` 关掉（迁移时写进
  Dockerfile / 启动代码）。
- OpenTelemetry：`capabilities=[Instrumentation(InstrumentationSettings(tracer_provider=自己的 TracerProvider,
  include_content=False))]`，只装 `opentelemetry-sdk`，不需要 logfire（只有 `logfire-api` 空壳）。得到的 span：
  `invoke_agent agent`、`chat deepseek-flash` ×2、`execute_tool find_city` ×2，属性是 GenAI semconv
  （`gen_ai.usage.*`、`gen_ai.tool.name`、`gen_ai.response.finish_reasons`…）；`include_content=False` 时 span 里
  没有用户文字、工具参数或结果（检查了 "Paris" 和用户消息标记）。不开 `Instrumentation` 就没有 span。

### 12 逐段缓冲文本、先过 ReplyGuard 再输出 ✅
在 `ModelRequestNode.stream` 的 `TextPartDelta` 上跑现有 `SentenceBuffer` + `ReplyGuard`，通过才 emit。
假服务器（`12_guard`）：`"Hello there. "` 放出；`"The price is $9,999.00."` 被拦（`number 9,999.00`），
异常从流里抛出 → 上游被关闭（`upstream_closed_by_client: true`），之后发模板。真 DeepSeek + 生产 prompt
（`12_guard_real`）：报价一轮，工具结果在 `FunctionToolResultEvent` 时 `guard.add_record`，回复全部放行。
注意：第一版用的 POC 简短 prompt，模型写了 Markdown 表格，被 guard 拦下（`number 1`）——这是 prompt 的事，
换生产 prompt 后正常；也说明 guard 在 SDK 下照常起作用。还需要自己补"后一步的文字前加空行"（现在 `_release` 做的）。

### 13 预算：哪些自带，哪些要窄适配 ⚠️
| 预算 | SDK 自带 | 语义是否一致 | 做法 |
|---|---|---|---|
| 步数 6 | `UsageLimits.request_limit` | 抛异常 vs 模板（可映射） | 或在 `iter` 里每个 `ModelRequestNode` 前问 `BudgetTracker.stop_before_step()`（已验证：第 6 次请求后停） |
| 每步工具 4 | 无 | — | 工具函数里先 `admit_tool_call()`，拒绝就回 `refusal()`（已验证：一次 6 个调用 → 4 个执行、2 个 `per_step` 拒绝，run 继续） |
| 每轮工具 12 | `tool_calls_limit` | 不一致（全拒、整轮抛） | 同上，用 `BudgetTracker` |
| 一次完成 >16 个调用 | 无 | — | `CallToolsNode` 拿到 `model_response.tool_calls` 后、执行前检查，离开 run（已验证：20 个调用 → 0 执行） |
| 单次输出 token | `max_tokens` | 一致 | settings |
| 每轮 token 软上限 | `total_tokens_limit` | 不一致（响应后抛、本步工具不跑） | 每步前用 `run.usage` + 估算判断 |
| 单次调用超时 60 s | `request_timeout`、`first_event_timeout` | 一致 | settings |
| 整轮 90 s | `iter(timeout=)` → `RunTimedOut` | 一致，但线程同 5 | `timeout` + `Threads.drain` |
| 重试 2 次 | openai SDK `max_retries`（拿到响应前） | 部分（流中途断不重试） | 见 10 |

都复用现有 `budget.py`，适配层在 `checks_fake.py:check_budgets` 里约 30 行。

### 14 deps 注入 user_id / conversation_id，模型无法伪造 ✅
`RunContext[Deps]` 不出现在工具 schema 里（发给模型的参数只有 `name` / `origin, destination` / `quote_id` /
四个过滤项）。假服务器让模型给 `list_my_tickets` 传 `{"user_id": "u_someone_else"}`：我们的校验回
`unknown argument(s): user_id`，两次调用用的都是 deps 里的用户。真 DeepSeek 被要求"传别人的 user id"时直接拒绝、
不调工具。

### 15 历史从 app.db 重建后作为 message_history ✅
真 DeepSeek（`15_history_from_app_db`）：第 1 轮用**现有 backend loop** 跑 "Quote Paris to Sydney please."（存入 app.db），
再存第 2 条用户消息，`build_context()` 得到 4 条 OpenAI 格式消息，用 `to_model_messages()`（`checks_real.py`，约 40 行：
user/system → `ModelRequest`，assistant → `ModelResponse(ThinkingPart(id="reasoning_content", provider_name="deepseek"),
TextPart, ToolCallPart)`，tool → `ToolReturnPart`）转成 `ModelRequest, ModelResponse, ModelRequest, ModelResponse`，
作为 `message_history` 跑第 2 轮 "Book it please."：模型调 `request_booking({"quote_id": "q_57b4…"})`，正是历史里的
quote，结果 `ok`；请求里 reasoning_content 原样回传（thinking + 工具，DeepSeek 没报错）。app.db 的存储和压缩不用变。

## 测量

| 项 | 值 | 来源 |
|---|---|---|
| 依赖数 | backend 运行时 22 个包 → 38 个（**+16**：pydantic-ai-slim、pydantic-graph、openai、httpx2、httpcore2、truststore、jiter、sniffio、tiktoken、regex、requests、urllib3、charset-normalizer、genai-prices、griffelib、logfire-api；pydantic / opentelemetry-api / anyio 已由 fastapi 带入） | `measure.sh` |
| 安装体积 | site-packages 32.7 MB → 71.6 MB（**+38.9 MB** 未压缩）；tar.gz 9.3 MB → 16.5 MB（**+7.2 MB** 压缩，镜像拉取增量约此数）。大头：openai 15.1 MB、pydantic_ai 11.8 MB、tiktoken 2.9 MB、genai_prices 1.7 MB | `measure.sh` |
| 冷启动 import | `import fastapi, httpx, networkx` 0.168 s → 加 `pydantic_ai` + `OpenAIChatModel` + `DeepSeekProvider` 0.488 s（**+0.32 s**，7 次中位数；前一次测得 0.160 → 0.449） | `measure.sh` |
| 首 token | 1.03 s vs 现有 1.22 s（n=10 交替，噪声内） | 1 |
| 取消 → 上游关闭 | 假服务器 1.6–1.8 ms（4 种方式各 7 次中位数）；真 DeepSeek 0.6 ms（3 次） | 4、6 |

`tiktoken` 和 `requests` 是 `[openai]` extra 带进来的，POC 里没有用到（没有 token 计数调用）。

## 迁移后 `backend/app/agent` 行数估算（按文件）

现在共 2237 行。按上面的接法（SDK 负责：HTTP 流、chunk 解析、tool-call 拼装、DeepSeek 请求字段、重试/单次超时、
工具分发、取消；我们保留：预算、线程 drain、会话锁和并发、ReplyGuard、模板、上下文、llm_calls 记录）：

| 文件 | 现有 | 删除 | 新增 | 保留 | 说明 |
|---|---|---|---|---|---|
| `llm.py` | 282 | ~230 | ~35 | ~50 | 删 httpx 客户端、SSE 解析、拼装、`_ThinkSplitter`、`LLMError`；新增 model/provider/settings 工厂；保留 `Completion.as_record` 形状（llm_calls）和 `wire_tool_call`（context 用） |
| `loop.py` | 495 | ~150 | ~130 | ~345 | 删 `_steps` / `_llm_step` 的 HTTP 重试和分发；新增 `iter` 驱动、事件 → SSE、`ModelResponse` → llm_calls 记录与计费、finish_reason 检查、异常映射（`ModelAPIError` / `UnexpectedModelBehavior` / `RunTimedOut` → 现有回退）、取消 token |
| `tools.py` | 199 | 0 | ~25 | 199 | `Tool.from_schema` 适配 + async 包装走 `Threads` |
| `budget.py` | 197 | 0 | ~10 | 197 | `UsageLimits` 语义不同，不替换 |
| `concurrency.py` | 181 | 0 | 0 | 181 | `Threads` 必须保留（5） |
| `context.py` | 189 | 0 | ~45 | 189 | OpenAI dict → `ModelMessage` 转换 |
| `config.py` | 74 | 0 | ~15 | 74 | effort → `ModelSettings`（或 `extra_body` 保持原字段） |
| `guard.py` `templates.py` `prompt.py` `sse.py` `__init__.py` | 620 | 0 | 0 | 620 | 不变 |
| **合计** | **2237** | **~380** | **~260** | **~1855** | **净减约 120 行（约 5%）** |

测试：`test_agent_llm.py`（379 行）大部分删除；现有 `FakeLLM` / `ScriptedLLM`（tests、evals）要包成 SDK 的
`FunctionModel(stream_function=...)` 适配（约 80 行新代码），`Finish("length")` 这类非 stop 结束原因能否在
`FunctionModel` 里表达未验证；`test_agent_loop.py`、`test_runtime_review.py` 里针对重试、估算计费、流中断的用例
需要改写，估计 100–200 行。

## 结论：对照阶段 1 通过条件

| 条件（plan.md） | 结果 |
|---|---|
| DeepSeek 上流式和多工具可用 | ✅ 1、2、7、15（要关掉自动 `strict: true`、改成串行执行） |
| 取消能关闭上游流、不再调用新工具 | ✅ 4、6（≤2 ms 关闭，无新请求）；⚠️ 已开始的同步线程不会被等待（5），要保留 `Threads` + drain |
| 能在我们的代码里在输出前做 ReplyGuard（逐段缓冲） | ✅ 12（`iter` + `node.stream`，guard 失败即关上游） |
| 每轮工具次数 / token / 步数 / 超时预算 | ⚠️ 13：超时和单次输出自带；步数、工具、token 的 SDK 语义都是"抛异常结束"，要用现有 `budget.py` 做窄适配（已验证，约 30 行） |
| 远程 trace 可关闭、可接自定义 processor | ✅ 11（默认无外发；只用 opentelemetry-sdk；banner 用环境变量关） |
| 迁移后维护的运行时代码明显减少，依赖增量可接受 | ❌ 净减约 120 / 2237 行（约 5%），同时新增五处适配（线程、预算、finish_reason、异常映射、历史转换）；依赖 +16 个包、+39 MB（压缩 +7 MB）、冷启动 +0.3 s |

**结论：技术上兼容（前五条在有窄适配的前提下都满足），但不满足"维护代码明显减少"。**SDK 替掉的只是 `llm.py`
的 HTTP/SSE 部分和 loop 里的分发，而我们代码量的大头（预算、并发和线程 drain、ReplyGuard、模板、上下文压缩、
llm_calls 记录）都要保留，有的还要加适配。仅就本 POC，不建议迁到 Pydantic AI。SDK 的取消确实好用（≤2 ms 关上游），但现有 loop 已经是后台 task + `aclosing`，
用 task cancel 做同类取消的成本本 POC 没有测，留给 ADR 和 OA 报告对照。是否采用由 ADR 结合 OpenAI Agents SDK 的报告决定。

## 风险

1. **发布节奏快、API 变动多**：2.71 里 `Agent(instrument=)` 已移除、`usage()` 改属性；迁移要钉死版本，升级要跑全部测试。
2. **取消时不等同步线程**（5），与源码注释不符；不保留 `Threads` 就可能在放锁后写 app.db。
3. **流在没有 finish_reason 时结束被当成 `stop`**（10）；适配层必须看 `provider_details['finish_reason']`。
4. **畸形参数重试耗尽 → 整轮抛 `UnexpectedModelBehavior`**（8），工具抛普通异常 → 整轮抛（3）；都要映射到模板。
5. **默认并行执行工具**（2）；漏设串行会让预算顺序、卡片顺序、guard 记录顺序变得不确定。
6. **自动 `strict: true`**（2）：DeepSeek 现在接受；换模型（#47 的多模型基准）时可能被拒，要显式关掉。
7. **thinking 字段不同**（7）：SDK 只发 `reasoning_effort`；DeepSeek 接受，但与现有请求不完全一致，可用 `extra_body` 保持原样。
8. **取消时没有 token 用量**（9）：与现在相同，继续用估算。
9. **两套 HTTP 栈**：backend 用 httpx，openai 3.x 用 httpx2；代理靠 `trust_env`，自定义 transport 时要自己传代理。
10. **测试替身要重写**：现有 `FakeLLM` 协议（OpenAI dict 进、`TextDelta`/`Completion` 出）要包成 `FunctionModel`。
11. `run_stream` + `StreamedRunResult.cancel()` 留下 "Task exception was never retrieved"（4）；用 `iter` + `run.cancel()`。
12. 启动 banner 打到 stdout（11），需 `PYDANTIC_AI_NO_BANNER=1`。

## 复现
```
cd spikes/pydantic-ai
uv sync --group otel
uv run python poc/checks_fake.py                                       # 无花费
OCEAN_ENV_FILE=<main checkout>/.env uv run python poc/checks_real.py   # 约 $0.01
OCEAN_ENV_FILE=<main checkout>/.env N_LATENCY=10 ONLY=1 uv run python poc/checks_real.py
./measure.sh
```
