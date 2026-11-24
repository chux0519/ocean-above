# STATE — #49 阶段 2–3：agent 运行时迁到 Pydantic AI Slim

更新：2026-11-24 · 分支 `it08-runtime`（已合并 origin/main `d123498`）· 报告：[report.md](report.md)

## 完成
- `backend/app/agent/`：`llm.py` 删除；新增 `model.py`（`ChatModel`、`Completion`、`LLMError`、`llm_error`、`wire_tool_call`、
  `ThinkSplitter`、`to_model_messages`）和 `scripted.py`（离线假模型：脚本 → OpenAI 兼容 SSE，经 httpx2 mock transport 走真实的
  Pydantic AI 模型）；`loop.py` 的步骤改由 `Agent.iter` + `node.stream` 驱动，并加了 `Agent.cancel()` 和 `tracer_provider` 入口。
  `budget.py`、`concurrency.py`、`guard.py`、`tools.py`、`templates.py`、`prompt.py`、`sse.py` 未改；`context.py` 只改 import。
- `backend/pyproject.toml` + `uv.lock`：`pydantic-ai-slim==2.71.0`、`openai>=3.31,<4`（不用 `[openai]` extra，tiktoken / requests 没装）。
- `backend/Dockerfile`：`PYDANTIC_AI_NO_BANNER=1`（依赖由 uv.lock 安装，其余不变）。本机无 docker，未 build。
- `routes.py`、`evals/__main__.py`：`OpenAIChatClient` → `ChatModel`。`evals/fake.py`、`bench/fake_llm.py`、`tests/agent_helpers.py`
  的假模型改为继承 `ScriptedLLM`（脚本写法不变）。
- 新测试 `tests/test_agent_runtime.py`（9 个）：流式中取消关流、不再请求；工具线程中取消等线程结束才放锁、后面的工具不跑；
  取消后下一轮正常；工具串行按序；模型不能指定用户；运行时没有出票工具；默认无 tracing / 无 logfire / banner 关；
  一轮内无任何外发连接；`tracer_provider` 能收到 run / chat / execute_tool span。
- 验证（源码 = 本分支 HEAD）：`HYPOTHESIS_PROFILE=ci` 全量 818 passed；`tool/test_diff.sh` 无新失败；`tool/review_lint.py` 通过；
  契约 fixture 测试在全量里通过；evals fake 31/31；前端 vitest 89 passed；真模型 evals 92/93、安全违规 0（见报告）。

## 改写的测试（旧断言 → 新断言 → 为什么）
| 测试 | 旧断言 | 新断言 | 为什么 |
|---|---|---|---|
| `test_agent_llm.py`（整文件） | 直接测 `OpenAIChatClient.stream()` 产出的 `TextDelta` / `Completion` / `LLMError` | 同样的 SSE 输入经 `Wire`（httpx2 mock transport → 真 `Agent` 一轮）检查请求体、`delta` 事件和落库的 `llm_calls` | 手写客户端已删除；意图（请求形状、分片拼装、reasoning 分离并回传、effort 上线、HTTP 错误可否重试且不泄露 key、断流可重试、内联 `<think>`、未闭合 think）逐条保留 |
| 同上 `test_http_errors` | `err.retryable` / `err.status` | 可重试的状态码请求 3 次（重试 2 次）、不可重试 1 次；记录的错误以 `LLM HTTP <status>` 开头；无 key；计费 0 | 行为在 loop 层可观察 |
| 同上 `test_reasoning_effort_from_config_and_per_request` | `client.body()` 的字段 | `ChatModel.settings()` 的 `extra_body` 字段 | 请求体由 SDK 组装，thinking 字段经 `extra_body` 发出 |
| 同上 `test_thinking_field_can_be_omitted_and_tools_left_out` | 无 tools 时不发 `tools` / `tool_choice` | 只测 omit（不发 thinking 字段） | loop 每次都带四个工具，"无工具请求"不存在 |
| 同上 `test_comment_lines_and_other_choices_are_ignored` | 忽略 index 1 的 choice | 忽略注释行，且请求不带 `n` | SDK 不跳过 index > 0；我们从不请求多个 choice |
| 同上 `test_unexpected_stream_content_becomes_a_retryable_llm_error` | 抛可重试 `LLMError` | 轮次以 `error` 结束、只留用户消息 | 实测：非 JSON、坏字节 → `ModelAPIError`，可重试（3 次请求）后 `llm_unavailable`；JSON 数组 chunk `[1, 2]` → SDK 内部 `AttributeError`，按 bug 走 `internal_error`（不重试、不记 llm_calls）。两种都不留半截回复 |
| 同上 `test_text_without_think_and_an_unclosed_think_block` | 客户端 completion 字段 | 落库字段相同；轮次 = 模板（`truncated`） | 与旧 loop 对 `length` 的处理一致 |
| 新增 `test_usage_is_stored_in_the_providers_shape`、`test_a_stream_without_finish_reason_is_not_a_reply`、`test_the_model_calls_the_server_not_the_sdk_retries` | — | — | 迁移引入的风险点 |
| `test_runtime_review.py::test_p1_2_the_client_reports_what_a_failed_call_received` | `LLMError.received` 为 None / None / 19 | 落库计费：无响应体的两种计 0；断流的按 19 个输出字符估算 | 同一意图（失败调用按收到的输出计费）在 loop 层检查 |
| `test_runtime_review.py::test_p2_2_no_retry_starts_once_the_token_cap_is_reached` | usage `{"total_tokens": 900}` | usage 带 prompt / completion tokens（合计 900） | SDK 只读 prompt / completion tokens，只给 total 的 usage 会丢（DeepSeek 总会给两者） |
| `test_agent_loop.py::test_reasoning_is_stored_passed_back_and_never_shown_or_guarded` | 只有工具调用的 assistant 消息 `content == ""` | `not content`（线上为 `null`） | SDK 的序列化；DeepSeek 接受 |
| `test_agent_loop.py::test_parallel_tool_calls_keep_their_grouping_in_the_history` | 没有 reasoning 的工具调用消息不带 `reasoning_content` 字段 | `not .get("reasoning_content")`（线上为 `""`） | DeepSeek 方言给工具调用消息补空 reasoning（SDK profile：缺字段时 DeepSeek 会 400） |
| `test_agent_loop.py::test_reasoning_effort_can_be_set_per_request`、`test_evals.py::test_reasoning_effort_reaches_every_llm_call` | 未指定 effort 时假模型看到 `None`，llm_calls 记 `None` | 看到并记录配置的默认值 `"high"` | 假模型现在从请求体读 effort，默认值会显式写在请求里；行为（每次调用都带本轮 effort）不变 |
| `test_agent_budget.py` | `from app.agent.llm import MAX_TOKENS` | `from app.agent.model import MAX_TOKENS` | 模块改名 |

其余测试（loop、预算、并发、runtime review、限额、HTTP、B1–B7 模型测试、evals）未改，全部通过。

## 评审发现 → 修法 → 测试（Fable，PR #53，[review-runtime-sdk.md](../review-runtime-sdk.md)）
源码：`1d7ceef`（之后只改文档）。全量 830 passed；`test_diff` 无新失败；lint 通过；evals fake 31/31；
真模型 N=1：30/31、安全违规 0、guard 回退 0、$0.0229、调用 p50 1203 ms、整轮 p50 2.43 s（[evals/real-after-review](evals/real-after-review/report.md)）。
唯一的失败 `zh_book_in_one_message`：模型第一次把中文城市名原样传给 `quote_route`（`unknown_city`），随后改用英文名报价并出确认卡，
最终结果正确；评测要求一次调用就对，所以记为失败。这是模型行为，不是运行时问题。

| 发现 | 修法 | 测试 |
|---|---|---|
| **P1-1** 重试次数跨步骤累计 | 计数放到 `_Turn.attempt`：一次调用成功就清零，失败重试时加一；`_steps` 只负责重开 run；记录里的 `attempt` 和退避都按步骤计 | `test_agent_loop.py::test_every_step_has_its_own_retries`（第 1 步失败 1 次、第 2 步失败 2 次 → 都恢复；退避 `[0.5, 0.5, 1.0]`；`attempt` `[1, None, 1, 2, None]`）、`test_retries_used_by_one_step_do_not_shrink_the_next` |
| **P2-1** 重复 `tool_call_id` → `internal_error` | `_unique_call_ids()`：拿到响应后、工具执行前，把重复的 id 改成 `id~2`、`id~3`（直接改 SDK 的 `ToolCallPart`，所以 SDK 执行、我们的记录和发回模型的消息用的是同一组 id） | `test_agent_runtime.py::test_repeated_tool_call_ids_still_run_every_call` |
| **P2-2** 白名单之外的流异常不记账、不重试 | `_llm_step` 在模型调用里捕获 `_GuardFailed` / `CancelledError` 之外的所有 `Exception`，映射成可重试的 `LLMError`；不认识的异常类型记 warning（带 traceback）。读到了响应体但不是 SSE 的情况按估算计费（`model.before_response()`：只有 HTTP 错误状态和连接失败才算"没有响应"） | `test_agent_llm.py::test_unexpected_stream_content_becomes_a_retryable_failed_call`（JSON 数组 chunk 也是 3 次请求，每次都有记录）、`test_a_200_response_that_is_not_a_stream_is_charged`；`test_agent_loop.py::test_a_plain_exception_*`（改写，见下）、`test_a_bug_outside_the_model_call_is_an_internal_error`（新增：保留 review #10 的本意） |
| **P2-3** 取消测试的"关流"断言恒真 | `scripted.py` 的响应体改成 `AsyncByteStream`，客户端关闭时 `aclose` 立即标记 `closed early`，发出 `[DONE]` 前先标记 `done`；断言放在轮次刚结束、对象还在的时候 | `test_cancel_while_the_model_streams_…`（加强）、`test_a_guard_failure_closes_the_stream_at_once`（新）、对照组 `test_a_normal_turn_reads_every_stream_to_its_end`（`["done", "done"]`）。**变异检查**：把 `aclose` 改成空操作（相当于"SDK 不再关流"）→ 取消和 guard 两个用例都红，对照组仍绿；改回后全绿 |
| **P2-4** SDK 加的 lookalike 提示 | **保留**：用户文字（或回显了用户文字的工具结果）里有 `<system>` 一类标签时，SDK 在该消息后面加一条 system 提示，说明那段是用户写的、不是应用指令。理由：只到模型，不进 SSE、不进 app.db，让伪造的 frame 更难生效；`llm_calls.request.messages` 仍记我们的上下文（缺 usage 时的估算少了这几十个词，偏差很小） | `test_agent_runtime.py::test_frame_lookalikes_in_user_text_get_the_sdks_note`（钉住发出的消息顺序和文案关键词；没有这类文字时不加） |
| **P2-5** 依赖 SDK 但没钉住的行为 | `end_strategy="graceful"` 显式写出；`iter(..., usage_limits=UsageLimits(request_limit=None))`，只用我们的步数预算；测试里用到 SDK 私有属性的地方加了注释"升级后失败先看这里" | `test_unknown_tools_step_after_step_end_at_the_step_budget`（连续 6 步未知工具 → `max_steps`）、`test_the_sdk_sets_no_request_limit_of_its_own`（`max_steps=20` 跑满 20 步）、`test_text_with_tool_calls_runs_the_tools`；重复 id 和 lookalike 见上 |
| **P2-6** openai 客户端的指纹头 | **去掉**：httpx2 请求钩子删除 `x-stainless-*`，`user-agent` 改为 `ocean-above`（迁移前的请求没有这些头） | `test_agent_llm.py::test_outbound_headers_are_the_ones_of_before` |
| **P2-7** 取消语义 | 不改，留给 #48（见"开放决定"） | — |
| **P2-8** 两处改弱的测试 | 坏流测试恢复成"3 次请求，每次都有记录，`llm_unavailable`"；`test_p1_2` 保持按估算计费的断言（`{"total_tokens": 0}` 不算有效回报，这个由 `test_p2_1_a_reported_total_of_zero_is_not_free` 覆盖） | 见 P2-2；评审列出缺少的四个用例都已补上（P1-1、P2-1、连续未知工具、`<system>` 文字） |
| **P2-9** 可删 / 重复 / 文档不符 | `llm_error` 去掉重复分支；删除没有调用方的 `provider="openai"` 方言（`OpenAIProvider` 也不再 import）；`model.py` 文件头改正（`<think>` 由 `ThinkSplitter` 处理）；`is_end_node` 分支和末尾 `return None` 合成一条断言；工具函数改成闭包工厂（模型参数里的 `_name` 不会再覆盖工具名）；`scripted.py` 去掉无用的 `except: raise`，模块头写明生产路径不会构造它；"离开就关流"的注释注明"真实连接上"。`got()` 和 `output_chars` 两处计数保留：前者用于中途失败的调用，后者用于完整调用的最终响应，已加注释 | 全量测试 |

改写的两个测试（补充到上表"改写的测试"）：
| 测试 | 旧断言 | 新断言 | 为什么 |
|---|---|---|---|
| `test_a_plain_exception_mid_stream_after_output_falls_back_and_keeps_the_card` | 模板，问题 `internal_error / AttributeError` | 模板，问题 `llm_error`（含 `AttributeError`），失败调用有记录、按估算计费 | P2-2：模型流里的任何异常都算失败的调用 |
| `test_a_plain_exception_before_any_output_is_an_internal_error` → `…_is_a_failed_call` | `error internal_error`，只存用户消息 | 重试 2 次（3 条记录，退避 0.5 / 1.0），然后 `llm_unavailable`，只存用户消息 | 同上；模型调用之外的 bug 仍是 `internal_error`，见新测试 `test_a_bug_outside_the_model_call_is_an_internal_error` |

### 留给 #48（P2-7）
- 取消后走模板 `_finish`：本轮已完成工具的卡片照常 `presented`，可以点击（与整轮超时相同，B2 / B3 不受影响）。用户按了 Stop
  之后是否还应出现可点的确认卡，由 #48 决定。
- 被取消打断的那个工具：线程跑完，quote 已建，但结果不进本轮、不出卡、不落 `tool_calls`。是否结算，由 #48 决定。
- `Agent.cancel` 目前只有测试调用，没有路由。

### ADR-001 修改草稿（协调者合并）
**Decision** 一节第一句改为：
> Adopt **Pydantic AI Slim** (`pydantic-ai-slim`, pinned) for the generic runtime: the model client (HTTP stream, chunk
> parsing, tool-call assembly, provider request fields), tool dispatch and run cancellation.

（去掉 "per-call retry / timeout"。）

**Boundaries** 一节加两条：
> - **Retries and per-call time limits:** ours. The SDK retries a streamed request only before any output reaches the
>   consumer; we retry while none of the call's text has been *released*, and log and charge every attempt (design §4.6,
>   §4.8). The provider SDK's own retries are off (`max_retries=0`); a retried step restarts the run from our context.
> - **What goes on the wire:** ours. Every request is rebuilt from the loop's context (`before_model_request`): wire-safe
>   tool calls, our JSON tool results, mid-conversation system messages kept as system messages, reasoning passed back
>   verbatim, no SDK announcement text, no SDK fingerprint headers. The one SDK addition we keep is its note after user
>   text that imitates a `<system>` frame (it reaches only the model).

**Consequences** 一节把 "Settings that must be explicit…" 那条换成 SDK 依赖清单和升级检查：
> - SDK hooks and settings we depend on (all public API): `Agent.iter` + `node.stream(run.ctx)`; `Tool.from_schema`
>   (`sequential=True`, `strict=False`); capabilities `before_model_request` (wire context) and `before_tool_validate`
>   (raw arguments); `ModelProfile.supports_inline_system_prompts=True`; settings `framing_announcement="never"`,
>   `max_retries=0`, `extra_body` (DeepSeek thinking fields); `end_strategy="graceful"`; `UsageLimits(request_limit=None)`;
>   `retries` high enough that the SDK never ends a turn (unknown tool names); `DeepSeekProvider` for every base URL;
>   `provider_details["finish_reason"]` (raw), `ModelResponse.usage` (+ `details`); `PYDANTIC_AI_NO_BANNER=1`.
> - Upgrade check: pin the new version, run the full suite (the tests that pin the above are in
>   `tests/test_agent_runtime.py`, `test_agent_llm.py`, `test_agent_loop.py`; tests touching private SDK attributes say so),
>   `spikes/pydantic-ai/poc/checks_fake.py`, and a real-model evals run (N=1, about $0.023).
> - Measured cost of the boundary: `backend/app/agent` grew from 2237 to 2665 lines (+428, of which 145 are the shared
>   offline fake), not the 5% reduction the POC estimated; see `docs/iterations/08-runtime/migration/report.md`.

## 进行中
无。

## 下一步
- 协调者：评审 PR；派 Fable 只读评审（出票安全、预算、取消、并发）；合并 design.md / README 草稿（下）；需要时在有 docker 的机器上 build 镜像记录大小。

## 开放决定
- #48：见上"留给 #48（P2-7）"。
- ADR-001 修改：见上"ADR-001 修改草稿"。

## 文档草稿（协调者合并）

### design.md §2 架构图
把 `agent/ (自写)` 子图改为：
```
    subgraph Agent["agent/"]
      Loop["agent loop（自写）<br/>预算 · 重试 · guard · 卡片 · 落库 · 取消"]
      Runtime["Pydantic AI Slim（ADR-001）<br/>Agent.iter · 工具分发 · OpenAIChatModel"]
      Tools["tools<br/>参数校验 + 分发"]
      Guard["reply guard<br/>数字 / 城市名按值溯源"]
    end
```
连线 `Loop <--> LLMC <--> LLM` 改为 `Loop <--> Runtime <--> LLM`，`Loop --> Tools` 改为 `Runtime --> Tools`。
模块边界加一条：
- `agent/` 的通用运行时（模型流、chunk 解析、工具分发、取消、OpenTelemetry span）是 Pydantic AI Slim（钉 2.71.0）；出票、
  ReplyGuard、app.db 历史、SSE 契约、预算和并发仍是我们的代码（ADR-001）。

### design.md §4 新增 §4.10 运行时（替换 §4.2 伪代码前的说明，伪代码不变）
> **§4.10 运行时（ADR-001，#49）** 每轮一个 Pydantic AI run：`Agent.iter(message_history=…)`，loop 在 `ModelRequestNode` 上
> 流式读事件（文本 → SentenceBuffer → ReplyGuard → `delta`），在 `CallToolsNode` 上执行工具。
> - 工具：`Tool.from_schema(TOOL_SCHEMAS)`，串行、按调用顺序，`strict` 关；执行体是 `Toolbox.run`，用户和会话来自 run 的
>   `deps`，阻塞部分走 `Threads`。参数不是 JSON 对象时原串交给 `Toolbox.run`（`invalid_arguments`）；未知工具名由 loop 记为
>   `unknown_tool`。
> - 请求：每次请求的消息都是 loop 自己的上下文（app.db 历史 + 本轮步骤，工具调用为 wire-safe 形式），会话中途的 system 消息
>   保持 system 角色；thinking 字段经 `extra_body` 发出，与迁移前相同；不加 SDK 的说明文字。
> - 重试：provider SDK 自身不重试；loop 在一步还没放出文字时按 §4.6 重试（每次尝试都记 `llm_calls` 并计费），从上下文重开 run。
> - 结束原因：读服务商原始 `finish_reason`；没有时当作断流（可重试），不当作 `stop`。
> - 取消：`Agent.cancel(user, conversation)` 取消本轮步骤任务；模型流立即关闭，不再有新的请求和工具调用；已开始的工具线程跑完、
>   drain 后才落库（模板，`cancelled`）并释放锁和名额。Stop 接口和界面见 #48。
> - Tracing：默认不装 instrumentation、无 exporter；`Agent(tracer_provider=…)` 给 #35 用（不带消息内容）。

### design.md §4.3 最后一行
"LLM 参数：`temperature=0`，`tool_choice=auto`；并行工具调用允许（按顺序执行）。" 后加："由 Pydantic AI 的 `OpenAIChatModel`（DeepSeek
方言）发出；`max_tokens` 同 §4.8。"

### README
第 30 行：
```
uv sync                                               # runtime: fastapi, uvicorn, networkx, pydantic-ai-slim (agent runtime, ADR-001); dev: pytest, hypothesis, httpx
```
架构 / 设计说明处加一句："The agent's generic runtime (model streaming, tool dispatch, cancellation) is Pydantic AI Slim
(docs/design/adr-001-agent-runtime.md); booking, the reply guard, history and budgets are our own code."

## 如何验证
```
cd backend
HYPOTHESIS_PROFILE=ci uv run pytest -q                 # 830 passed
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
cd backend && uv run python -m evals --mode fake        # 31/31
OCEAN_ENV_FILE=<main checkout>/.env uv run python -m evals --mode real --n 1 --effort high --out /tmp/x   # ≈ $0.023 / 轮
cd ../frontend && npx vitest run                        # 89 passed
```
