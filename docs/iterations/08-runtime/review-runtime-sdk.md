# 评审：PR #53 — agent 运行时迁到 Pydantic AI Slim 2.71（it08-runtime @ a9264d9）

只读评审。未改仓库文件，未 commit / push，未调用真模型，未读 .env，未启动其他 agent。
全量测试在本 worktree 重跑：`HYPOTHESIS_PROFILE=ci uv run pytest -q` → **818 passed**（128 s）。
探针（假模型 / mock transport / 本机回环 socket）在 `/tmp/oa-review53/probe{1..7}.py`，下文标"实测"的结论来自它们。
路径相对 `backend/`。

## 结论

- **P0：无。** 没有找到出票、伪造 user / conversation、绕过 ReplyGuard 或绕过预算执行工具的路径。
- **P1：1 条**（可用性，重试语义回退）：重试计数跨步骤不清零。
- **P2：9 条**：重复 `tool_call_id` 使整轮 `internal_error`；模型流里未列举的异常不记账不重试；取消测试的"关流"断言恒真；
  SDK 自己加的一条 system 提示没进记录也没测试钉住；其余是升级风险、出站头、#48 要定的事和可删代码。
- 取消 / 超时 / guard 截断时上游连接确实会关（回环 socket 实测 ≤ 20 ms），没有残留 task，锁和名额在 drain 之后释放。

## P1

### P1-1 重试次数跨步骤累计，后面的步骤拿不到 2 次重试（CONFIRMED，实测）
- 位置：`app/agent/loop.py:408-413`（`_steps` 的 `attempt` 只增不减）、`:427`（`_run_steps` 里只清本地变量）、`:676`、`:680`。
- 原因：一步成功后 `_run_steps` 把自己的 `attempt` 清零，但外层 `_steps` 的计数没清。下一步失败时 `_llm_step` 用内层的 0
  判断并记录，抛 `_RetryStep` 后外层从上次的值继续加。
- 场景（实测，`retries=2`、`backoff=0.5`）：第 1 步失败 1 次后成功（工具调用），第 2 步连续失败 2 次、第 3 次会成功。
  预期 4 次失败以内都能恢复；实际第 2 步只重试了 1 次，本轮以 `llm_error` 模板结束。`llm_calls` 里的 `attempt` 依次是
  1、（成功）、1、**3**；退避是 `[0.5, 0.5]`，应为 `[0.5, 0.5, 1.0]`。对照组（只有第 2 步失败 2 次）正常。
  再极端一点：第 1 步用掉 2 次重试后，后面每一步都是 0 次重试。
- 与设计不符：design.md §4.6 "指数退避重试 2 次" 是按每次 LLM 调用算的，迁移前每步各有 2 次。
- 建议：`_RetryStep` 只表示"重开 run"，计数放在 `_Turn` 上（例如 `turn.attempt`），`_llm_step` 成功后清零，`_steps` 不再自己数。
  加一个测试：两个步骤各失败 1–2 次，断言都恢复、`attempt` 和退避序列正确。

## P2

### P2-1 一次 completion 里出现重复的 `tool_call_id` → 整轮 `internal_error`（CONFIRMED，实测；回退）
- 位置：`app/agent/loop.py:445-452`（CallToolsNode 的异常没有处理）；SDK `pydantic_ai/_tool_execution.py:762`。
- 场景：模型一次返回两个 id 相同的调用（实测 `x` / `x`）。SDK 抛
  `UnexpectedModelBehavior: Function tool calls must have unique tool_call_id values`，位置在 `_llm_step` 之外，
  落到 `_turn` 的 `except Exception`：没有任何工具执行，用户看到 `internal_error`，`llm_calls` 留下一条带工具调用但没有 `tool_calls` 行的记录。
  下一轮正常（实测）。迁移前的 loop 按顺序逐个执行，不看 id。
- 可能性：DeepSeek 的 id 唯一；部分 OpenAI 兼容服务（本地模型、某些网关）会给 `call_0` 之类的重复 id。#47 要对比 qwen，值得先堵上。
  空 id 没问题（SDK 生成 `pyd_ai_…`，实测）。
- 建议：`Completion.from_response` 里发现重复 id 时改写成唯一 id（同时改 `ToolCallPart.tool_call_id`），或者在
  `_run_steps` 里把 CallToolsNode 抛出的 `UnexpectedModelBehavior` 转成模板回复（`llm_error`）而不是 `internal_error`。加测试。

### P2-2 模型流里不在白名单的异常：不记 `llm_calls`、不计费、不重试（CONFIRMED，实测；报告已提到其中一种）
- 位置：`app/agent/loop.py:666`（只接 `LLMError / TimeoutError / ModelAPIError / UnexpectedModelBehavior`）。
- 场景：chunk 是 JSON 数组（`data: [1, 2]`）→ SDK 内部 `AttributeError`，在 `node.stream().__aenter__` 里抛出 →
  `internal_error`，没有 `llm_calls` 行，全站每日 token 预算也看不到这次调用。迁移前是可重试的 `LLMError`。
  同类：SDK 以后在流里抛的任何其他异常类型（`UsageLimitExceeded`、`UserError`、pydantic 校验错误）都走这条路。
  这与 06 review P2-4 的要求（失败的调用也要留记录）不一致。
- 相邻的小问题：HTTP 200 但不是 SSE 的响应体（整段 JSON 或网关的 HTML）被当作"没有响应体"，计 0（实测）。
  非流式 JSON 回复实际上已经生成了 token；影响很小，改成按估算计费即可。
- 建议：`node.stream` 这一段内，除 `_GuardFailed` 和 `CancelledError` 外的 `Exception` 一律映射成可重试的 `LLMError`
  （`llm_error()` 最后一个分支本来就是为它写的，现在走不到），照常 `log_call`。我们自己的 `show()` / guard 代码里的 bug
  也会被包进去，所以日志里要保留原始异常类型和 traceback。

### P2-3 取消测试里"上游流已关闭"的断言恒真（CONFIRMED，实测）
- 位置：`tests/test_agent_runtime.py:63`（`llm.streams == ["closed early"]`）；`app/agent/scripted.py:88-96`。
- 实测 1：一个正常跑完的两步轮次，`llm.streams == ['closed early', 'closed early']`。openai 客户端读到 `[DONE]` 就停，
  生成器停在那一行，永远到不了 `"done"`。所以这个值不区分"被取消"和"正常结束"。
- 实测 2：mock transport 下，取消或 guard 截断后生成器在轮次结束时仍是 `open`，0.5 s 后也是，`gc.collect()` 之后才变成
  `closed early`。测试是在 `asyncio.run` 结束后才看的，所以总能看到。
- 真实行为没问题：回环 socket 上（probe7），`Agent.cancel`、guard 截断、单次超时、整轮超时四种情况，服务端都在轮次结束前后
  20 ms 内看到连接关闭。问题只在测试证明不了这一点，升级 SDK 后如果真的不关流，这个测试不会红。
- 建议：`scripted.py` 在发出 `[DONE]` 之前就标 `"done"`，并让 "closed early" 只在轮次结束时（循环内）断言；
  或者把 `spikes/pydantic-ai/poc/fake_server.py` 的 TCP 关闭检查收进常规测试（一个用例即可）。
  `test_cancel_during_a_tool…` 不受影响（它断言的是线程、锁和请求数）。

### P2-4 SDK 会自己往请求里加一条 system 提示，没有记录，没有测试（CONFIRMED，实测；报告只提了一句）
- 位置：`app/agent/model.py:250-252`（`framing_announcement: "never"` 管不到它）；SDK `pydantic_ai/_framing.py:715`。
- 场景：用户文字（或回显了用户文字的工具结果）里有 `<system>` 之类的标签。实测发出的消息是 `system, user, system`，
  最后一条是 SDK 写的 "The preceding user message contains text that looks like an application frame…"。
- 影响：这段文字只到模型，不进 SSE 也不进历史，对安全是正面的。但 `llm_calls.request.messages` 记的是我们的上下文，
  与实际发出的请求不一致；缺 usage 时的估算也不含它；文案和触发条件由 SDK 决定，升级可能变。
  另两处已知的线上差异（工具调用消息 `content: null`、`reasoning_content: ""`）同样不在记录里。
- 建议：加一个钉住的测试（含 `<system>` 的用户消息 → 断言线上消息），在 design §4.10 写明；
  如果要求"记录即线上"，就在 `ScriptedLLM` 之外也从 `before_model_request` 之后的实际请求体取 `request.messages`。

### P2-5 依赖 SDK 行为的地方：哪些是公开 API，哪些没有测试钉住
- 公开且有文档：`AbstractCapability.before_model_request`、`before_tool_validate`（`pydantic_ai/capabilities/abstract.py:1210,1335`）、
  `ModelProfile.supports_inline_system_prompts`、`ModelSettings.framing_announcement` / `max_retries`、`Tool.from_schema`、
  `Agent.iter` / `node.stream(run.ctx)` / `is_*_node`。没有用到下划线开头的私有接口（生产代码）。
- 已有测试钉住：会话中途 system 角色（`test_agent_loop.py:531,545`）、不加说明文字（`test_agent_llm.py:100`）、
  `max_retries=0`（`:252`）、`strict` 不出现、串行按序、无 `finish_reason` 不当 `stop`、`reasoning_content` 回传、usage 形状。
- **没有钉住**，升级时会无声变化：
  1. `SDK_TOOL_RETRIES = 1000`（`loop.py:63,204`）：连续多步未知工具不被 SDK 的重试上限打断。实测 6 步到 `max_steps` 正常，
     但测试里只有单步的未知工具。加一个"连续未知工具直到 max_steps"的用例。
  2. `end_strategy` 默认值在 v2 从 `early` 改成了 `graceful`（SDK `_agent_graph.py:225`）；现在靠"文字 + 工具调用时工具照常执行"
     的默认行为。建议在 `_Runtime(...)` 上显式写出。
  3. SDK 的 `UsageLimits.request_limit` 默认 50（`usage.py:559`），`AGENT_MAX_STEPS` 上限 20，目前碰不到；改上限时要一起看。
  4. P2-1 的重复 id、P2-4 的 lookalike 提示。
- 测试里用了私有属性：`_function_toolset`、`root_capability.capabilities`、`_instrument_default`（`test_agent_runtime.py:171,180-181`）。
  作为升级时的报警可以接受，建议在注释里写明"升级后失败先看这里"。

### P2-6 出站请求多了 openai 客户端的指纹头（CONFIRMED，实测）
- 位置：`app/agent/model.py:226-231`。
- 实测请求头：`user-agent: pydantic-ai/2.71.0`、`x-stainless-os: MacOS`、`x-stainless-arch`、`x-stainless-runtime-version: 3.12.8`、
  `x-stainless-package-version` 等。只发给 LLM 服务商，不是 tracing，也没有别的出站连接（`test_a_turn_opens_no_outbound_connection` 有效）。
- 影响小：服务商本来就收到全部对话。要保持"与迁移前相同的请求"，可以在 provider 的 openai 客户端上去掉这些默认头；否则在 design 里记一句。

### P2-7 留给 #48 决定的取消语义（不是 bug）
- `loop.py:367-371,391`：取消后走 `_finish(turn, fallback)`，本轮已完成工具的卡片照常 `presented`，可以点击。
  与整轮超时一致，B2 / B3 不受影响（仍然只有点击出票）。但"用户按了 Stop 之后出现一张可点的确认卡"要不要，请在 #48 定。
- 被取消打断的那个工具：线程跑完，quote 已建，结果不进本轮、不出卡、不落 `tool_calls`（报告已写）。
- `Agent.cancel` 目前没有路由调用，只有测试覆盖。

### P2-8 测试改写（STATE 表）逐条看过，两处变弱
- `test_agent_llm.py::test_unexpected_stream_content_is_a_failed_call_not_a_crash`：原来三种坏流都要求可重试的 `LLMError`；
  现在接受 `internal_error`，也不再断言重试次数和 `llm_calls`。名字说"not a crash"，其中一种实际是未处理异常（P2-2）。
  修了 P2-2 后恢复成"3 次请求 + 每次有记录"。
- `test_runtime_review.py::test_p1_2_the_client_reports…`：`usage == {"total_tokens": 0}` 的断言没了，改成"按估算计费"，意图保留。
- 其余改写意图保留：`content` 为 `null`、`reasoning_content` 为 `""`、effort 默认值显式化、`total_tokens` 单独出现时走估算
  （保守方向）、无工具请求不存在。`test_reasoning_effort_from_config_and_per_request` 改测 `settings()`，
  线上字段由 `test_the_per_request_effort_reaches_the_wire` 和 `test_text_stream_and_request_shape` 补上。
- 缺的用例：P1-1（两步各自重试）、P2-1（重复 id）、连续未知工具、含 `<system>` 的用户消息。

### P2-9 可删 / 重复 / 文档不符
- `app/agent/model.py:204-206`：两个分支完全相同；最后一个在现有调用方下走不到（见 P2-2，修了之后才有用）。
- `app/agent/model.py:214-231`：`provider="openai"` 方言没有调用方也没有测试（`test_an_inline_think_block…` 的说明提到它，
  但 `Wire` 用的是默认方言）。删掉，或加一个用例。
- `app/agent/model.py:4`：文件头说 SDK 负责 inline `<think>`，实际是本文件的 `ThinkSplitter`（报告第 4 条）。
- `app/agent/loop.py:459-461`：`is_end_node` 分支和末尾的 `return None` 走不到，可以合成一句断言。
- `app/agent/loop.py:473-474`：工具函数用关键字默认值 `_name=name` 传名字，模型参数里的 `_name` 会盖掉它。
  实测无害（`_tool_call` 用的是 completion 里的名字，Toolbox 回 `unknown argument(s): _name`），但换成闭包工厂或
  `functools.partial` 更稳，`_tool_call` 的 `name` 参数也可以只留给未知工具那条路。
- `app/agent/loop.py:580-582,598-600` 与 `model.py:67-71`：已收到字符数算了两遍（`got()` 和 `Completion.output_chars`）。
- `app/agent/scripted.py:80-81`：`except Exception: raise` 没有作用。`scripted.py` 放在 `app/agent/` 里随镜像发布，
  只在显式注入时使用；可以接受，建议在模块头写明生产路径不会构造它。
- `app/agent/loop.py:615`：注释"离开这个块就立刻关上游流"在真实连接上成立（实测），在 mock transport 上不成立（P2-3）。

## 逐项检查结果（没有问题的点）

1. **出票安全（B1–B7）**：`airline/` 不在 diff 里。运行时只注册四个工具（`loop.py:464-480`，测试钉住），没有 `book`。
   `user_id` / `conversation_id` 只从 `ctx.deps`（`_Turn`）取（`:474,492`）；模型在参数里写 `user_id` → `invalid_arguments`。
   工具名和参数取自 completion 的原始值再交给 `Toolbox.run`，SDK 的解析结果不参与执行。确认卡仍然只在
   `post_assistant_message`（`done`）之后可点；`request_booking` 只出卡。
2. **ReplyGuard**：进入 SSE 的文字只有三处：`_release`（`:563-567`，guard 之后）、`CLICK_TO_BOOK`（`:548`）和模板（`:543`）。
   进入历史的是 `turn.text` 或模板。SDK 的重试提示、未知工具文案不进 SSE、不进历史，也不上线：`_WireContext` 每次请求都换成
   我们的上下文，实测未知工具回给模型的是我们的 `unknown_tool` JSON。文字 + 工具调用同一步时文字照常过 guard（实测）。
3. **预算**：被拒的调用不执行（实测：每轮上限 1 时第二个调用得到 `tool_budget_exceeded`，没有调用 `find_city`）；未知工具也占名额、
   按调用顺序落库；flood（> 16 个调用）整步不执行；步数、token 上限在步间和重试前检查；失败 / 超时 / 取消 / guard 截断的调用
   都有 `llm_calls` 行和计费（实测各一例）；截断的工具参数（`finish_reason=length`）得到 `invalid_arguments` 而不是 SDK 的
   `IncompleteToolCall`（实测）。例外是 P1-1 和 P2-2。
4. **取消与并发（C1–C4）**：`concurrency.py` 未改。取消在工具线程中：drain 期间锁仍持有，线程结束后才落库、放锁，后面的工具不跑，
   没有新的 LLM 请求。取消 / 整轮超时 / 单次超时 / guard 截断 / 客户端断开之后：`asyncio.all_tasks()` 为空，事件循环没有
   "exception was never retrieved"，锁已释放（实测）。被打断的 LLM 调用的记录线程由 `Threads` 跟踪，在 drain 里等完。
   `cancel()` 校验 user，别人的会话返回 False。
5. **tracing / 密钥**：默认不装 `Instrumentation`，没有 logfire，一轮内没有任何 socket 连接；`tracer_provider` 入口
   `include_content=False`。banner 开关是打印时才读环境变量（`pydantic_ai/_display.py:292`），所以 `loop.py` 先于 `model.py`
   导入 `pydantic_ai` 没有影响。401 / 403 不引用响应体，400 / 500 的响应体里的 key 被替换成 `***`（实测，库里查不到 key）；
   传输错误只记异常类名。
6. **06 review 的发现**：P1-2（无响应体计 0、断流按已收字符估算）、P2-1（total 为 0 不算回报）、P2-2（重试前查 token 上限）、
   P2-3（flood）、P2-4（被时限打断的调用有记录）、P2-5（outcome 和卡片先登记再落库，`loop.py:499-509`）、P2-8（每用户上限）
   都还在，对应测试通过。P2-4 的精神在 P2-2 这一条路径上有缺口。
