# #49 阶段 2–3：agent 运行时迁到 Pydantic AI Slim — 报告

日期 2026-11-24 · 分支 `it08-runtime` · 依据 [ADR-001](../../../design/adr-001-agent-runtime.md) · STATE：[STATE.md](STATE.md)

## 结果一览

| 项 | 06（迁移前，`P1/evals/after`） | 迁移后（本分支） |
|---|---|---|
| 真模型 evals（deepseek-flash，high，31 例 × 3） | 100%（93/93） | **98.9%（92/93）** |
| 安全不变量违规（stream / no_agent_booking / click_books_once / no_false_booked / provenance） | 0 | **0** |
| ReplyGuard 回退 | 0 | 1（`booking_claim`，被 guard 拦下改成模板） |
| 工具成功率（四个工具） | 100% | 100% |
| LLM 调用数 | 396 | 397 |
| 花费 | $0.0671 | **$0.0449（第 2、3 轮）+ 第 1 轮约 $0.023 ≈ $0.068**（见"花费"） |
| LLM 调用延迟 p50 / p90 | 1167 / 1610 ms | 1073–1102 / 1473–1564 ms |
| 整轮延迟 p50 / p90 | 2.35 / 3.29 s | 2.18–2.27 / 3.0–3.41 s |
| 每轮 token p50 / p90 / max | 4,192 / 12,989 / 27,298 | 4,247–4,323 / 12,707–12,815 / 25,757–26,135 |
| 后端测试 | 798 passed（06） | **818 passed**；`test_diff` 无新失败；lint 通过 |
| evals fake 模式 | 31/31 | 31/31 |
| 前端 vitest | — | 89 passed（前端未改） |

唯一的失败（第 1 轮 `quote_details`）：用户只要求列出航段，模型多调了一次 `request_booking`（只出确认卡，什么都没订，
回复里也写明"点击才出票"）。这是模型行为的波动，与运行时无关；第 2、3 轮同一用例通过。原始报告：[evals/real-pass1](evals/real-pass1/report.md)、[pass2](evals/real-pass2/report.md)、
[pass3](evals/real-pass3/report.md)。

## 做了什么

| ADR 边界 | 实现 |
|---|---|
| SDK 管模型客户端（HTTP 流、chunk 解析、tool-call 拼装、请求字段） | `model.py` 的 `ChatModel`：`OpenAIChatModel` + `DeepSeekProvider`（所有 base URL 都用 DeepSeek 方言，即旧客户端的请求格式），httpx2 客户端，超时同旧值。`llm.py` 删除 |
| SDK 管工具分发 | `loop.py` 用 `Agent.iter` + `node.stream` 驱动；四个工具 `Tool.from_schema(TOOL_SCHEMAS[...])`，`sequential=True`、`strict=False`，执行体仍是 `Toolbox.run`；`user_id` / `conversation_id` 来自 `deps`（`_Turn`） |
| SDK 管取消 | `Agent.cancel(user_id, conversation_id)`：取消本轮的 steps 任务 → SDK 关上游流、不再开始新请求和工具；已开始的工具线程走 `Threads`，drain 后才落库、放锁。不做 run_id / Stop 接口 / 前端（#48） |
| Tracing | 默认不装 `Instrumentation`，无 exporter、无 logfire；`Agent(tracer_provider=...)` 是给 #35 的入口（`include_content=False`） |
| ReplyGuard 在输出前 | 文本事件 → `SentenceBuffer` → `ReplyGuard` → `delta`，不变；guard 失败直接离开流，上游即关 |
| app.db 历史 | `build_context`（不变）→ `to_model_messages()` → `message_history` |
| SSE 契约 | 不变；契约 fixture 测试通过 |
| 预算、并发、锁、drain、llm_calls、缺 usage 的估算 | 全部沿用，语义不变（`budget.py`、`concurrency.py` 未改） |
| 异常映射 | `llm_error()`：`ModelHTTPError` → 按状态码判断可否重试；`ModelAPIError` / `UnexpectedModelBehavior` → 可重试；其他异常（bug）照旧走 `internal_error` |
| 无 finish_reason 不当 stop | 读 `provider_details["finish_reason"]`，为空 → `LLMError("LLM stream ended without a finish_reason")`，与旧行为相同 |
| `strict` 关、thinking 字段同旧、无 banner | `tool.strict = False`；thinking 走 `extra_body`（`{"thinking": {"type": ...}, "reasoning_effort": ...}`）；`PYDANTIC_AI_NO_BANNER=1`（代码 + Dockerfile） |

### 与 ADR 预期不同、需要协调者知道的地方
1. **重试仍是我们的。** ADR 写"per-call retry 由 SDK 负责"。实测 SDK 只在流输出到达消费者之前重试，而旧语义是"还没有
   放出文字就重试"（缓冲中的 `Hel` 仍可重试），且每次尝试都要落 `llm_calls`、计费、重试前查 token 上限。所以 openai 客户端
   `max_retries=0`，失败的那一步从我们的上下文重新开一个 `iter` 再试（`_RetryStep`）。测试意图全部保留。
2. **请求上下文仍以我们的为准。** 用 `before_model_request` 能力（`_WireContext`）把每次请求的消息换成 loop 自己的上下文
   （`turn.messages`）。原因：SDK 自己的历史会把坏的工具名（`weird tool!`）原样发回（review #11 修过的问题），把未知工具的
   结果写成它自己的文字，`[booking event]` 这类会话中途的 system 消息会被改成 user 消息里的 `<system>` 块，还会在前面加
   约 120 词的说明。现在线上请求与迁移前一致（只多一处：用户文字里出现 `<system>` 一类标签时，SDK 会在前面加一条提示）。
3. **两个工具边界情况靠 hook。** 参数不是 JSON 对象 → `before_tool_validate`（`_RawArguments`）把原串交给 `Toolbox.run`，仍回
   `invalid_arguments`；未知工具名 → SDK 先拒，loop 从结果事件补记 `unknown_tool`（准入、落 `tool_calls`、回给模型的是我们的 JSON）。
4. **`<think>` 内联拆分保留。** SDK 只认单独成块的 `<think>` 标签；整段在同一个 chunk 里时会当成正文（guard 会拦下，但回复变模板）。
   旧的 `ThinkSplitter` 移到 `model.py` 继续用。
5. **代码量没有减少。** `backend/app/agent` 2237 → 2589 行（+352），其中 132 行是测试 / evals / bench 共用的离线假模型
   （`scripted.py`，旧的假模型分散在测试里）。不算它是 +220。POC 估计 −120，差别来自上面 1–4 条：重试重开、上下文替换、两个 hook、
   未知工具补记、工具结果按调用顺序落库、取消接口。按文件：`llm.py` −282；`model.py` +296；`loop.py` 495 → 699；
   `scripted.py` +132；`__init__`/`context.py` 小改。

评审修复（Fable，PR #53）之后：2665 行（+428，其中 `scripted.py` 145 行；不算它是 +283）。修复后真模型 N=1：30/31、安全违规 0、
回退 0、$0.0229，见 [evals/real-after-review](evals/real-after-review/report.md) 和 STATE"评审发现 → 修法 → 测试"。

## 依赖与体积

| 项 | main | 本分支 |
|---|---|---|
| 运行时依赖（安装的包） | 22 | 33（+11：pydantic-ai-slim 2.71.0、pydantic-graph、openai 3.58.0、httpx2、httpcore2、truststore、jiter、sniffio、genai-prices、griffelib、logfire-api） |
| tiktoken / requests | — | **没装**：依赖写成 `pydantic-ai-slim==2.71.0` + `openai>=3.31,<4`，不用 `[openai]` extra（它只多出 tiktoken，SDK 只在 embeddings / realtime 里 import） |
| site-packages | 32.7 MB（tar.gz 9.3 MB） | 65.3 MB（tar.gz 14.6 MB）：**+32.6 MB / 压缩 +5.3 MB** |
| `import app.main`（`-X importtime` 累计） | — | 0.43 s，其中 pydantic_ai 0.16 s + openai 0.10 s（约 +0.27 s） |
| 镜像 | 本机没有 docker，未 build；镜像增量按上面 site-packages 估计约 +33 MB（压缩层约 +5 MB） | — |

## 花费
真模型合计约 **$0.07**（上限 $0.30）：第 2 轮 $0.0226、第 3 轮 $0.0223（准确）；第 1 轮和 3 例冒烟测试是在修好 usage 形状之前跑的，
evals 报告把缓存命中当成未命中、按峰值价算，报出的 $0.132 + $0.0057 是上限；按 token 量（与第 2、3 轮相同量级）估计实际约 $0.025。
修复见 STATE（`llm_calls.usage` 恢复服务商原样字段，加了测试）。N=3 拆成三次 N=1 跑，是因为 evals 的预估上限（$0.60）超过本次
$0.30 的花费上限，每跑一轮核对一次实际花费。

## 风险与后续
- **取消时被打断的工具结果不记录**（与整轮超时相同，design §4.9）：线程跑完，但结果不进本轮、不出卡、不落 `tool_calls` 行。
  #48 要决定是否结算这类结果。
- **只有 `total_tokens` 的 usage 会丢**：SDK 只读 prompt / completion tokens；DeepSeek 总会给出两者。
- **形状错误的 chunk**（如 JSON 数组）SDK 抛内部异常，loop 当 bug 处理（`internal_error`，不重试、不记 llm_calls）；旧客户端当可重试错误。
  非 JSON 和坏字节仍是可重试错误。
- **`choices` index > 0** 不再被忽略：请求从不带 `n`，实际不会出现。
- 线上请求两处无害差异：只有工具调用的 assistant 消息 `content` 为 `null`（原来 `""`）；DeepSeek 方言给没有 reasoning 的工具调用
  消息补 `reasoning_content: ""`。真模型 93 次运行无 400。
- **SDK 2.x 更新快**：已钉 2.71.0；升级时跑全量测试和 `spikes/pydantic-ai/poc/checks_fake.py`，并复核上面 1–4 条依赖的 hook
  （`before_model_request`、`before_tool_validate`、`supports_inline_system_prompts`、`framing_announcement`）。
