# Implementation — 03-slice T3 agent loop, tools, ReplyGuard, SSE events (issue #2)

- Source state: `it03-agent` @ `f53dfad` 全套测试；合并 main（T5 #13）后的 `e72dc5d` 上重跑：420 passed，test_diff 无新失败，lint 通过。
- 环境：macOS，uv，Python 3.12.8。**无新增依赖**；`httpx` 从 dev 依赖移到运行依赖（agent 运行时用它调 LLM），`uv.lock` 同步。
- 范围：`backend/app/agent/**`、`backend/tests/test_agent_*.py`、`backend/tests/agent_helpers.py`、本目录；
  另改 `backend/pyproject.toml` / `uv.lock`（仅 httpx 那一行）。没有改 airline / store / core / 契约 / `main.py`。

## 1. 做了什么（`backend/app/agent/`）

| 文件 | 内容 |
|---|---|
| `config.py` | `LLMConfig.from_env()`：`LLM_API_KEY`（回退 `DEEPSEEK_API_KEY`）、`LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`LLM_MODEL`（默认 `deepseek-flash`）、`LLM_REASONING_EFFORT`（默认 `high`）。先读进程环境，再读 `OCEAN_ENV_FILE` 指向的 `.env`（默认仓库根 `.env`）。key 不进 repr、不进日志。 |
| `llm.py` | `OpenAIChatClient`：纯 httpx 的 OpenAI 兼容流式 client。`stream(messages, tools, reasoning_effort=None)` 逐段给 `TextDelta`，最后给一个 `Completion`（分片的 tool call 参数拼完整、usage、reasoning）。所有失败都是 `LLMError(retryable)`；错误信息里的 key 被抹掉，401/403 不引用响应体。 |
| `tools.py` | 4 个工具的 schema（参数只有名字和 id，全是字符串，`additionalProperties:false`）、手写参数校验、`Toolbox.run` 分发。失败（未知工具 / 参数不合法 / 工具抛异常）都是给 LLM 的结构化记录，不抛出。 |
| `guard.py` | `CityIndex`（在文本里找库内城市名）、`ReplyGuard`（数字 / 日期时间 / id / 城市按值溯源）、`SentenceBuffer`（流式按句切分）。 |
| `templates.py` | `fallback_reply(outcomes)`：只用本轮工具结果拼出的模板回复。 |
| `context.py` | `build_context`：从 app.db 重建 LLM 上下文（消息 + 历史工具调用 + 出票事件 + reasoning 回传），超预算时压缩最早的轮次。 |
| `prompt.py` | system prompt（v3，见 §4）。不含城市列表。 |
| `loop.py` | `Agent.stream_turn(user_id, conversation_id, text, *, reasoning_effort=None)`：一轮对话 → 契约 SSE 事件。 |
| `sse.py` | `encode(event)` / `encode_stream(events)`：`event: …\ndata: …\n\n`。 |

### 一轮对话
1. `stream_turn` **同步**检查：文本非空且 ≤ 2000 字符、`reasoning_effort` 合法、会话属于该用户（否则 `ValueError` / `NotFound`，
   此时什么都没落库）。然后起一个独立 task 跑这一轮，返回事件的异步迭代器。**客户端断开后这一轮照常跑完并落库。**
2. task 拿会话锁（`airline.locks.hold_async`，与出票接口同一把）→ 存 user 消息 → `user_message` 事件 → 会话无标题时用首条消息前 60 字符做标题。
3. 最多 `MAX_STEPS = 6` 步：调 LLM；有 tool call 就逐个 `status` → 执行 → `tool_calls` 落库 → 每张卡 `card` 事件 → 结果回给 LLM；
   没有 tool call 就是最终文本。
4. 文本按句放行：每放行一句前，用 ReplyGuard 检查「**到目前为止的全文 + 这一句**」（能抓到跨句的 `St. | Louis`、被切开的数字）。
   通过 → `delta`；不通过 → `replace`（模板）并结束。空白不单独发送。
5. 结束：`airline.post_assistant_message(...)`（quote 卡此刻 `presented` 并拿到 nonce）→ `done`。
6. 每 15 s 没有事件就发 `ping`。

| 情况 | 结果 |
|---|---|
| 某句没通过 guard | `replace` 模板 + `done`；`messages.guard_problems = [{"kind": "number"/"city"/"date"/"time"/"id", "value"}]`；被拒原文存在 `llm_calls.response`（`aborted: true`） |
| LLM 返回空文本 | 模板 + `done`，`guard_problems = [{"kind":"empty_reply"}]` |
| 到 `MAX_STEPS` 仍在调工具 | 模板 + `done`，`[{"kind":"max_steps"}]` |
| LLM 超时 / 5xx / 429 / 断流 | 指数退避重试 2 次（0.5 s、1 s）；**本次调用已有文本放行后不重试**（否则会重复已显示的内容） |
| 重试用尽，本轮还没有任何记录和文本 | `error {"code":"llm_unavailable"}`，user 消息保留，不存 assistant 消息 |
| 重试用尽，本轮已有工具结果 / 已放行文本 | 模板 + `done`（卡片不丢），`[{"kind":"llm_error"}]` |
| 401 等不可重试错误 | 不重试，同上两行 |
| 会话锁 120 s 内拿不到 | `error {"code":"conversation_busy"}`，什么都不存 |
| 其他异常（含 locations.db 损坏导致建不了城市索引） | `error {"code":"internal_error"}`，日志里有堆栈 |

### ReplyGuard 规则（design §4.5）
- **数字**：文本里每个数字（去千分位后按 Decimal 比较）必须等于：工具结果里任何字符串中的数字（显示值）、结果里的数值
  （`count` 等）、**结果里任何列表的长度**（航段数、中转数、票数——由代码数）、或用户**本条**消息里的数字。
  有序列表序号（行首 `1.`）不算数字。
- **id**（`q_…`、`t_…`）：必须是记录或用户消息里出现过的。
- **日期 / 时间**：ISO、`November 23, 2026`、`23 Nov`、`08:15` 等写法解析成值，必须等于记录里的时间戳、用户消息里的日期或今天。
- **城市**：文本里出现的库内城市名（大小写 / 重音 / 空白不敏感，弯引号和不间断连字符也认）必须在记录里，或用户在
  本会话的消息里提到过。写法与库内名字完全一致时按那个城市算（`San Jose` ≠ `San José`）；只是折叠后相同
  （`SAN JOSE`）时两个都算候选。单词城市名只在首字母大写时才算（`nice` / `split` / `cork` 也是普通单词）。
- reasoning（thinking 的思考内容）**不是回复文本**，不经过 guard，也不发给用户。
- 模板回复只用记录拼句子；有一条属性测试保证「任意一组工具结果的模板都能通过由同一组结果建的 guard」。

### agent 没有出票路径
- 工具只有 4 个；`Toolbox` 只保存 4 个绑定方法，`Agent` 只保存 `post_assistant_message`、`locks`、`networks`、`store`，
  都不保存 `Airline` 对象。包内没有任何名为 `book` 的属性访问（AST 测试）。
- nonce 不进 LLM：上下文只用存下来的工具结果（`Result.data` 去掉 30 位高精度值），不用 `Airline.messages` 的卡片。

## 2. 给 T5（HTTP API）的接口

```python
from app.agent import Agent, LLMConfig, OpenAIChatClient, encode_stream
from app.agent.sse import MEDIA_TYPE, HEADERS
from app.airline import NotFound

llm = OpenAIChatClient(LLMConfig.from_env())      # 启动时建一个；LLMNotConfigured = 没有 key
agent = Agent(airline, llm)                        # 与出票接口共用同一个 Airline 实例（同一把会话锁）

@app.post("/api/conversations/{cid}/messages")
async def post_message(cid: str, body: dict, user = Depends(current_user)):
    try:
        events = agent.stream_turn(user["id"], cid, body.get("text"))   # 可选 reasoning_effort="none|low|high|max"
    except NotFound:    ...404 not_found
    except ValueError:  ...400 bad_request
    return StreamingResponse(encode_stream(events), media_type=MEDIA_TYPE, headers=HEADERS)

# 关闭时：await agent.wait_idle(); await llm.aclose()
```
- `stream_turn` 必须在事件循环里调用（`async def` 路由）；它返回前已做完归属和文本检查，所以 404 / 400 能在发响应头之前返回。
- 事件：`Event(name, data)`，`data` 就是契约里的 JSON。顺序：`user_message` → (`status` / `card` / `delta` / `replace` / `ping`)* →
  恰好一个 `done` 或 `error`。
- SSE `error` 的 code：`llm_unavailable`、`conversation_busy`、`internal_error`（契约没有枚举 SSE 的 code；前两个与 HTTP 错误码同名）。
- 限额（每用户每日消息数、全站预算）不在 T3：`llm_calls.tokens` 已按调用落库（`usage.total_tokens`，含 reasoning token），可据此累计。
- `.env.example` 目前没有 `LLM_REASONING_EFFORT` 和 `OCEAN_ENV_FILE`（T7 的文件，没有动）；需要协调者加上名字。
- 给 T4（评测）：`store.list_tool_calls(user_id, user_message_id)`（名字、参数、结果 code）、`messages.guard_problems`
  （是否回退及原因）、`llm_calls`（请求、响应、reasoning、被拒原文、`request.turn` = 该轮 user 消息 id、`request.reasoning_effort`）。
  脚本化假 LLM 在 `backend/tests/agent_helpers.py`（`FakeLLM`、`Call`、`Think`、`Wait`、`assert_stream`）。

## 3. Thinking / reasoning effort（协调者 2026-11-23 补充需求）

按 DeepSeek 官方文档（`api-docs.deepseek.com/guides/thinking_mode`、`/guides/tool_calls`、`/quick_start/pricing`，2026-11-23 读取）
并在真实 API 上逐条验证：

| 项 | 文档 | 实现 |
|---|---|---|
| 模型 id | `deepseek-flash`（= DeepSeek-V4.1-Flash；`GET /models` 确认） | `LLM_MODEL` 默认值 |
| 开关 | `{"thinking": {"type": "enabled" / "disabled"}}`，默认 enabled | effort `none` → `disabled`；其余 → `enabled` |
| 强度 | `{"reasoning_effort": "low" / "high" / "max"}`，默认 high | `LLM_REASONING_EFFORT`（默认 `high`）；`none` / `low` / `high` / `max`；另有 `omit`（两个字段都不发，给不认这些字段的其他 OpenAI 兼容服务，只能由部署配置） |
| 按请求覆盖 | – | `client.stream(..., reasoning_effort=)` 和 `agent.stream_turn(..., reasoning_effort=)`；`None` = 配置默认值。非法值 `ValueError`。 |
| `temperature` | thinking 模式下不生效（不报错） | 仍然发 `temperature: 0`；**thinking 开启时输出不是确定性的** |
| 流式输出 | 思考内容在 `delta.reasoning_content`，与 `content` 同级 | 单独累积，不产生 `TextDelta` → 不发给用户、不进 ReplyGuard |
| 存档 | – | 原样存 `llm_calls.response.reasoning_content` |
| 回传 | 请求带 `tools` 时，之前各轮的 `reasoning_content` **应当回传**（否则可能 400）；不带 tools 时会被忽略 | 同一轮内：tool call 的 assistant 消息带着 reasoning 回传。跨轮：`build_context` 从 `llm_calls` 把 reasoning 放回它所属的 assistant 消息，并恢复并行 tool call 的原始分组。 |

- 以下 assistant 消息**不带** reasoning：被模板替换的回复（用户没看到那段文字，对应的思考不回传）、被压缩的旧轮次、
  当时 thinking 关闭的轮次。实测（8 个请求，约 $0.001）：历史里缺 `reasoning_content`、为空串、thinking 关闭时仍带着它、
  对话中间出现 `system` 消息，API 都返回 200。文档说可能 400，所以能回传的都回传。
- `max_tokens` 设为 4096（含 reasoning token）。

## 4. 真模型实测（issue 的「第一步」）

脚本 `docs/iterations/03-slice/T3/guard_probe.py`：真实 agent loop + 真实 `locations.db` + 临时 app.db，31 个场景 50 轮对话
（多航段 / 直飞 / 问数字 / 无服务 / 歧义 / 找不到 / 同城 / 缺起点 / 下单 / 拒绝 / 查票 / 中文 / 无关问题，以及 6 个诱导
计算或用常识的场景：换算英里、合计、取整、欧元和半价、「Dubai 就在 Abu Dhabi 旁边吧」、平均和最长航段）。
`CLICK` 步骤由脚本代替用户点击（走 `Airline.book`）。模型 `deepseek-flash`，2026-11-23。

| 运行 | effort | prompt | 轮数 | 原文发出 | 回退 | LLM 调用 | token（缓存命中 / 未命中 / 输出，其中 reasoning） | 费用（按高峰价） |
|---|---|---|---|---|---|---|---|---|
| 1 | none | v1 | 40 | 40 | 0 | 74 | 103,040 / 16,449 / 3,286 | $0.0095 |
| 2 | none | v2 | 50 | 50 | 0 | 90 | 135,296 / 20,883 / 3,847 | $0.0117 |
| 3 | none | v3 | 50 | 49 | 1 | 90 | 135,296 / 22,103 / 3,804 | $0.0120 |
| 4 | **high** | v3 | 50 | 49 | 1 | 92 | 147,328 / 20,067 / 8,043（3,817） | $0.0166 |
| 5 | **high** | v3 + guard 改动 | 50 | **50** | **0** | 92 | 150,144 / 19,154 / 8,455（4,041） | $0.0168 |

- 费用：5 次合计约 **$0.067**，加上零星联调不到 $0.08（预算 < $1）。价格取文档高峰价（输入未命中 $0.3 / 命中 $0.006 /
  输出 $1.2 每百万 token），非高峰减半。一轮对话约 2 次 LLM 调用、3.4k token，约 $0.0003。
- 延迟（effort high）：单次 LLM 调用中位数 1.1 s，p90 1.7 s，最大 3.6 s。
- 结果文件：`guard_probe_results.json`（运行 5）、`guard_probe_results_effort_none.json`（运行 3），含每轮的工具调用、回复原文、guard 问题。
- 所有运行里工具选择都符合预期；否定 / 疑问句没有触发 `request_booking`；6 个诱导场景模型都没有自己算。出票只发生在脚本的 `CLICK`。

**两次回退各是什么：**
- 运行 3：模型自己算了差价（「so the difference is $168.78」）→ guard 拦下，换成模板。**正确拦截。**
- 运行 4：模型说「I can quote Dubai to London」，London 是用户**上一轮**说的城市，不在任何记录里 → **误伤**。
  据此把城市规则放宽为「用户在本会话任何一条消息里提到过的城市」（数字仍只认本条消息）。运行 5 无回退。

**prompt 的三版：**
- v1 → v2：`san jose` 被模型自动改成 `San Jose`（精确命中，歧义没有问用户）→ 要求城市名按用户原样传入；
  加了 `similar_names` 时要告知用户；禁止工具调用前的铺垫文字（「I'll look up both routes.」）；价格规则只说「与距离成正比，卡片可看计算依据」。
- v2 → v3：中文城市名（「广州」）被原样传给工具 → 把「非拉丁文字用英文名」放在「原样传入」之前并举例；
  回复里出现字段名（「the card shows `transfer_count` 3」）→ prompt 不再点字段名，并禁止带下划线的词。
- **结论**：回退率 0–2%，保留「文字可以照抄显示值」的方案，不需要退到「文字里不写任何数字」。

**实测里看到、guard 管不了的（已知限制）：**
- 模型**比较**两个有出处的数（「Guangzhou to Paris is cheaper: $991.93 versus $1,160.71」「最长的是 Shanghai → Singapore 14,777.2 km」）：
  每个数都能溯源，guard 放行。effort none 时出现 2 次，high 时模型拒绝比较。要堵只能加「比较」工具或禁止并列两个价格。
- 用文字写的数（「two stops」）、不在库里的城市、小写的单词城市名（设计 §4.5 已列）。
- `find_city` 命中有服务的城市时没有卡片，模型有一次说「the card shows its connections」。

## 5. 需要协调者知道的决定

1. **城市规则比 design §4.5 宽**：用户在本会话**任何一条**消息里提到的库内城市都可出现在回复里（设计写的是「用户本条消息」）。
   原因见 §4 运行 4。数字没有放宽。同意的话 design §4.5 需同步。
2. **可见记录 = 上下文里的全部工具结果 + 出票事件**（不只本轮），所以「刚才那个报价多少钱」可以照抄上一轮的显示值。
3. **列表长度算作可溯源的数**（航段数、中转数、票数）；给 LLM 的报价结果里另加了 `leg_count` / `transfer_count`
   （在 `agent/tools.py` 里由代码数出来）。建议以后挪进 `Airline._quote_data`。
4. **给 LLM 的结果去掉了 30 位高精度的 `distance` / `price`**（旁边有 `*_display` 时），LLM 只看到显示值。
5. **城市名来源**：guard 需要全部库内城市名，取自 `airline.networks.current().cities`（airline 的公开属性，没有 import core）。
   建议 airline 加一个 `city_names()`。折叠函数是 `core.places.fold` 的副本，有测试保证两者在全部 711 个城市上一致。
6. **LLM 不可用且本轮没有任何产出时不存 assistant 消息**（契约：`error`，user 消息保留）。design §4.1 的状态机画的是
   Fallback → Persist；现在只有「已有记录或已放行文本」时才走模板 + `done`。下一轮上下文里会有连续两条 user 消息（实测 API 接受）。
7. **工具调用前的铺垫文字照常放行并保留**在最终消息里（与后面的文字用空行隔开）。流式下无法预知后面有没有 tool call。
8. **会话标题**：T3 在首轮用首条消息前 60 字符设置（`store.set_conversation_title`）。T5 如另有打算可以去掉。
9. **出票事件进上下文**：会话里的 system 消息（`Ticket booked.` + 票卡）以 `role: system` 的
   `[booking event] {...}` 放进历史，模型据此知道票已出（实测模型会说「Your Madrid–Cairo ticket is confirmed」）。
10. **历史预算** 40,000 字符（约 1 万 token）；超出时最早的轮次文本截到 300 字符、工具结果只留 code 和 id。
11. **thinking 默认 high 时 `temperature=0` 不生效**（文档），评测的可重复性会受影响；T4 做 bench 时可用
    `reasoning_effort="none"` 对照。
12. 同一轮里先报价再 `request_booking` 会有两张卡（行程卡 + 确认卡）指向同一个 quote，都可点，票只会有一张（T2 的设计）。
13. `locations.db` 损坏时整轮 `internal_error`（guard 的城市索引建不出来）。没有降级成「不查城市」，因为那样 guard 就形同虚设。

## 6. 测试（新增 149 个；全套 394 passed，约 62 s）

| 文件 | 数量 | 内容 |
|---|---|---|
| `tests/test_agent_guard.py` | 47（21 个函数） | 数字（差一分、取整、换算、求和、数错、格式写坏、用户说过的数）、日期时间、id、城市（常识城市、大小写 / 重音 / 弯引号、`San Jose` vs `San José`、最长匹配、跨句）、早先用户消息里的城市、折叠函数与 core 一致、按句切分（200 种随机分块拼回原文）、**模板必过 guard**（真实库上 21 种工具结果 + 60 组随机组合） |
| `tests/test_agent_llm.py` | 37（16 个函数） | mock transport：文本流、请求体、分片 tool call 拼装、reasoning 分离与存档、effort 的配置 / 按请求覆盖 / `omit`、HTTP 错误码是否可重试、**错误信息里没有 key**、超时 / 断流 / 坏 JSON；env 文件；工具 schema、参数校验、`Toolbox` 分发和失败 |
| `tests/test_agent_loop.py` | 47（42 个函数） | 见下 |

`test_agent_loop.py`（脚本化假 LLM；airline、app.db、真实 `locations.db` 副本都是真的），对应 issue 的验收项：
- **事件顺序 / 契约**：询价轮的事件序列与 `contract/fixtures/sse_quote_turn.txt` 相同；每个事件的形状用 T2 的 fixture
  形状校验器检查；`card` 里 `bookable=false`，`done` 里才有 nonce；delta 拼起来 = 最终文本。
- **工具分发**：并行 tool call 按序执行、每个各推卡片、tool 消息与 call id 对应；无工具回复；铺垫文字。
- **参数不合法**：缺参数 / 不是 JSON / 夹带 `price` → 结构化错误回给 LLM → 自行修正；未知工具；工具异常。
- **guard 失败 → replace**：错的价格（第一句已发出，第二句失败，第三句不发）、常识城市 Dubai、跨 chunk 的数字、
  结尾没有标点的错句、空回复、上一轮的数字可溯源。
- **MAX_STEPS**：6 步后模板，同一句不重复 6 遍。
- **LLM 错误**：重试 + 退避（0.5 s、1 s）、用尽 → `llm_unavailable` 且 user 消息保留、401 不重试、工具结果之后失败 → 模板、
  已放行文本后断流不重试、调用挂住 → 超时。
- **agent 不能出票**：AST 结构检查；6 个出票样的工具名都是 `unknown_tool`；反复 `request_booking`（含夹带 nonce）不出票；
  nonce 从不出现在任何 LLM 请求里；**12 个种子的随机脚本**（任意工具名、任意参数、已知和编造的 id，共 290 多次工具调用，
  其中出票尝试 80 多次）之后票数为 0、`Airline.book` 调用 0 次。
- **上下文**：下一轮从库里重建历史（工具调用、结果、LLM 原文）；用户点击后的出票事件进上下文；超预算压缩但保留 id。
- **thinking**：reasoning 落库、同轮和跨轮回传、不展示、不经 guard；模板回复不带 reasoning；并行 tool call 分组还原；按请求覆盖 effort。
- **流**：慢 LLM 时有 `ping`；客户端断开后本轮照常落库且卡片可订；**出票点击等待进行中的轮次**（同一把锁，
  超时是 `conversation_busy`）；同会话两轮串行；拿不到锁 → `conversation_busy`。
- **输入检查**：别人的会话 / 不存在的会话 / 空文本 / 超长文本在落库前拒绝；Bob 拿不到 Alice 的 quote。

## 7. 验证

```
bash tool/test_diff.sh            # no new failures (0 known)
python3 tool/review_lint.py       # R01 ok, R02 ok
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q    # 394 passed（1 个既有的 starlette 弃用警告）
cd backend && uv run pytest -q -W error tests/test_agent_guard.py tests/test_agent_llm.py tests/test_agent_loop.py   # 131 passed
```
真模型实测重跑（花钱，约 $0.02）：
`cd backend && OCEAN_ENV_FILE=<主 checkout>/.env PYTHONPATH=. uv run python ../docs/iterations/03-slice/T3/guard_probe.py out.json`

开发中遇到并修掉的一个问题：`Agent.wait_idle` 最初用 `gather` 等已结束的 task，会空转（测试挂住）；改成只等未结束的 task。
本 worktree 没装前端依赖，`test_diff` 的前端部分没有实际跑到；本任务没有改前端文件。

## 8. Round 02（评审 PR #14 后的修改，被测提交 `ebf8594`）

评审报告：只读评审（Fable），无 P0，4 条 P1、13 条 P2。协调者决定：P1 全修；P2 修 #5 #6 #7 #8 #9 #10 #11 #13 #14 #15 #16 #17，#12 按 (b) 改契约。

| # | 问题 | 修改 | 测试 |
|---|---|---|---|
| 1 | 数字前是字母 / `_` 时不检查（`OA1234`、`RMB6800`、`_950 USD_`） | 任何数字串都检查，只排除前面是数字 / `.` / `,` 的（同一个数的后半段）；不符合 id 格式的 `t_xyz123` 里的数字也检查 | `test_adversarial_replies_are_caught` |
| 2 | `_Dubai_` 不算城市 | 城市边界改为「前后不是字母或数字」 | 同上 |
| 3 | 工具回显 LLM 自己写的参数被当成记录 | `query`、`filter`、`message`、`available` 不进 guard；`not_found` / `invalid_arguments` / `unknown_tool` / `tool_failed` / `bad_request` 的结果整条不进；模板也不再复述 `query` | `test_values_echoed_from_the_llms_own_arguments_are_not_records` |
| 4 | 「已出票」类虚假说法 | 新规则 `booking_claim`：记录里（上下文全部记录）没有用户点击出票事件或非空的查票结果时，「ticket is booked / I've booked / booking confirmed / 已为您出票 / 预订成功 / 订好了」等肯定说法 → 模板；「Nothing is booked until you click」「only issued once you click」等不算。本轮 `request_booking` 成功时，代码在回复末尾固定追加 `CLICK_TO_BOOK`（作为一个 `delta`） | `test_a_false_booking_claim_never_reaches_the_user`（4 种说法 × 断言文本不出现在任何事件里）、`..._after_request_booking_...`、`test_after_the_users_click_the_llm_may_say_it_is_booked` |
| 5 | 行首任意 1–2 位数字算列表序号 | 只有从 1 开始连续的 `1. 2. 3.` 才算 | 对抗表 |
| 6 | 数字不看单位；任意列表长度都算 | `$x` / `x USD` / `x 美元` 只能匹配价格显示值，`x km` / `公里` 只匹配距离显示值；后跟 miles、hours、k、thousand、%、PM、万、小时 等一律失败；计数只认 `count`、`leg_count`、`transfer_count` 字段 | 对抗表、`test_honest_replies_still_pass` |
| 7 | 非拉丁文回复里的城市不检查；用户小写普通词放行同名城市 | prompt：任何语言的回复里城市名都用记录里的英文原名，不写航班号 / 订票号 / 时刻 / 其他币种；用户消息里的单词城市名也要首字母大写才算。**局限**：模型若仍把城市译成中文（「迪拜」），guard 看不出——城市索引只有库内拉丁文名；靠 prompt 和评测约束 | `test_user_lowercase_words_do_not_allow_one_word_cities` |
| 8 | 中文每句之间被插空行 | 只在一个 LLM 步骤的第一段文字前加段落分隔 | `test_chinese_sentences_keep_the_llms_layout`、`test_text_of_two_steps_is_still_two_paragraphs` |
| 9 | 不看 `finish_reason` | 没有 tool call 且 `finish_reason != "stop"`（`length`、`content_filter` 等）→ 模板，`kind: truncated` | `test_a_truncated_reply_is_replaced_by_the_template` |
| 10 | 流里的普通异常没有收尾 | client 把流解析中的任何非预期异常转成可重试的 `LLMError`；轮次里已有工具结果或已放行文本时，任何异常都走模板 + `done`（`kind: internal_error`），卡片不丢；什么都没产出时仍是 `error internal_error` | `test_unexpected_stream_content_...`、`test_a_plain_exception_mid_stream_...`、`..._before_any_output_...` |
| 11 | 非法工具名 / 非 JSON 参数原样回传 | `wire_tool_call`：回传给 API 时名字不合 `^[A-Za-z0-9_-]{1,64}$` 改为 `invalid_tool`，参数不是 JSON 对象改为 `{}`（同轮和历史重建都用它）；原样值只留在 `tool_calls` 表 | `test_tool_calls_go_back_...`、`test_a_broken_tool_call_does_not_poison_later_requests` |
| 12 | `conversation_busy` 与契约不符 | 契约已改为 (b)（协调者 `e68f26e`）；锁等待 120 s → **10 s** | busy 测试按契约断言 |
| 13 | 深嵌套 JSON → `RecursionError` | 参数原文超过 2000 字符直接拒绝；`RecursionError` 也算非法 JSON | `test_deeply_nested_or_huge_arguments_...` |
| 14 | 上下文没有硬上限 | 超过 80,000 字符时丢弃最早的（已缩短的）轮次，换成一条 system 消息列出它们的 quote / ticket id（id 仍可溯源）；给 LLM 的票最多 20 张（`count` 仍是全量，加 `tickets_shown`） | `test_the_history_has_a_hard_limit_and_keeps_the_ids`、`test_ticket_lists_for_the_llm_are_capped_...` |
| 15 | 内联 `<think>` | client 把 `content` 开头的 `<think>…</think>` 移进 reasoning（跨 chunk、未闭合都处理）。关于回传范围：DeepSeek 文档明确要求带 tools 的请求回传**之前各轮**的 reasoning（文档示例的 Turn 2 也回传 Turn 1 的），所以保留跨轮回传 | `test_an_inline_think_block_...`（3 种切分）、`test_text_without_think_...` |
| 16 | 一轮最长约 36 分钟 | 整轮总时限 **90 s**（单次 LLM 调用 60 s）；超时：已有产出 → 模板 + `done`（`kind: turn_timeout`），没有产出 → `error llm_unavailable` | `test_a_turn_has_a_total_time_limit` |
| 17 | 测试缺口 | 上面各行；`FakeLLM` 加 `Finish(reason)`；对抗样本表 31 条 + 正常回复 10 条 | — |

**接口变化（T5 注意）**：`stream_turn` / `encode_stream` / `Event` 签名不变。`Agent(...)` 新增关键字参数 `turn_timeout`（默认 90），
`lock_timeout` 默认 120 → 10，`llm_timeout` 默认 120 → 60。`messages.guard_problems` 新增 kind：`booking_claim`、`truncated`、
`internal_error`、`turn_timeout`。`request_booking` 成功的轮次在 `done` 前多一个 `delta`（`\n\n` + 点击提示）。

**真模型复测**（effort high，同 31 场景 / 50 轮，更严的 guard）：50/50 原文发出，0 回退，0 个 `booking_claim`；LLM 调用中位数 1.1 s；
$0.016。结果 `guard_probe_results_round02.json`。本任务真模型总费用约 $0.09。

**验证**（`ebf8594`）：全套 491 passed；agent 三个文件 `-W error` 202 passed；计时相关测试连跑 5 次均通过；`tool/test_diff.sh` 无新失败；lint 通过。
