# Ocean Above 订票助手 — 设计文档（草稿 v2，2026-11-23）

> 状态：v2 = v1 + 设计评审（Fable 5.2，2 P0 / 7 P1 / 9 P2）全部按推荐修改，用户已确认推荐方案。
> 中文工作稿；最终交付版（英文）按 take-home 要求的 5 个标题组织。冲突时以 `Quotr Agent Dev Engineer take-home.md` 为准。

## 0. 一句话

用户用自然语言订票。**LLM 只负责理解意图、选择工具、组织语言；路线、距离、价格、票据全部由确定性代码从
`locations.db` 和业务库算出，并以带出处的记录（record）形式存在。** 用户看到的每个数字和城市都能在记录里找到，
记录可以追溯到具体的数据库行和公式。**出票只能由用户点击确认按钮触发**，LLM 没有出票能力。

## 1. 设计原则

### 1.1 Report 与 compute 分离（参考 FSR, arXiv:2610.25503）

FSR（*From Seeing to Reporting*）指出：模型能逐条列出正确的记录，但在同一个回答里自己「汇报」的汇总数字却会出错
（173 个记录全对，自报的计数加起来却是 188）。它的做法是把回答建模为「回答 → 解码出的记录 → 构造出的集合 → 任务答案」
这条链，**最终答案由代码从记录推导，而不是让模型直接说出来**。

| FSR 的链条 | 本项目 |
|---|---|
| 模型输出的记录（records） | LLM 只产出**结构化意图**：工具调用和参数（城市名、quote id、筛选条件） |
| 解码 / 构造集合 | 确定性代码：解析城市 → 最短路 → 航段集合 |
| 任务答案（task answer） | 由代码从航段记录推导：总距离 = Σ 航段距离，价格 = 总距离 × 0.13，票数 = 记录数 |
| 汇报（reporting） | 事实以**卡片**呈现（完全由记录渲染）；LLM 文本里的每个数字、每个城市名都要能在记录里找到，否则不发出 |

硬规则：
- **P1 LLM 不算数**：距离、价格、航段、票数、中转城市、最近有服务城市都来自工具返回的记录。
- **P2 文本按值溯源**：LLM 文本里的每个数字必须等于本轮可见记录里的某个显示值；每个库内城市名必须出现在本轮记录
  或用户消息里。否则不发出这段文本（§4.5）。
- **P3 每条记录可溯源**：记录里存数据来源（locations 行、hub_links 行、库指纹）、公式和高精度值；界面可展开
  「这个价格怎么来的」。
- **P4 出票只由用户点击触发**：LLM 只能「请求下单」（让界面出确认卡），出票接口只接受用户点击带来的一次性确认凭证；
  出票时代码对 quote 重新推导，和存档比对（§6）。

### 1.2 其他原则
- `locations.db` 是唯一事实来源，只读打开，不复制数据；文件指纹一变就重载。
- **高精度计算（50 位）和存储，只在显示时舍入**；全系统只有一个规范的计价函数（§5）。
- 依赖最小化，agent loop 自己写；图算法用 networkx。
- **领域边界（为将来扩展预留，现在不上 MCP）**：航空公司侧能力（城市、报价、出票、查票，将来的时刻 / 座位 / 库存）
  集中在 `airline/` 服务接口后面；agent 只依赖这个接口。将来可原样拆成 MCP server 或接真实订票系统。
- 流式输出：卡片在工具返回后立即推送，文本按句校验后推送（§4.7）。
- 先交付必交物：03 结束时就有可本地运行的端到端版本，README 和设计文档每个迭代增量更新。

## 2. 架构

```mermaid
flowchart LR
  subgraph Browser["浏览器 · Next.js + shadcn/ui + Tailwind"]
    UI_Chat["聊天页<br/>消息 + 行程卡 / 确认卡 / 票卡"]
    UI_Tickets["我的机票"]
    UI_History["历史会话"]
  end

  subgraph Backend["FastAPI 后端（单进程）"]
    API["api/<br/>HTTP 路由 · SSE · 归属检查 · 限流"]
    subgraph Agent["agent/"]
      Loop["agent loop（自写）<br/>预算 · 重试 · guard · 卡片 · 落库 · 取消"]
      Runtime["Pydantic AI Slim（ADR-001）<br/>Agent.iter · 工具分发 · OpenAIChatModel"]
      Tools["tools<br/>参数校验 + 分发"]
      Guard["reply guard<br/>数字 / 城市名按值溯源"]
    end
    Airline["airline/ 服务接口<br/>报价 · 确认凭证 · 出票复核 · 查票<br/>(将来可拆为 MCP server)"]
    Auth["auth<br/>固定账号（开发）<br/>Google ID token（公开前）"]
    subgraph Core["core/ (纯函数, 确定性)"]
      Net["network<br/>加载 · 校验 · 指纹"]
      Route["routing<br/>hub 全对最短路 + 端点枚举"]
      Price["pricing<br/>规范计价函数"]
      Places["places<br/>城市解析 · 最近有服务城市"]
    end
    Store["store/<br/>sqlite3, 无 ORM"]
  end

  LDB[("locations.db<br/>只读 · 唯一事实来源")]
  ADB[("app.db<br/>用户 · 会话 · 消息 · 调用记录<br/>报价 · 机票")]
  LLM["LLM<br/>DeepSeek flash"]

  UI_Chat & UI_Tickets & UI_History --> API
  API --> Loop
  API -->|"用户点击确认（凭证）"| Airline
  Loop <--> Runtime <--> LLM
  Runtime --> Tools --> Airline --> Core
  Loop --> Guard
  API --> Auth
  Auth -.->|"核验令牌 (JWKS)"| Google["Google Identity"]
  Net --> LDB
  Route --> Net
  Price --> Net
  Places --> Net
  Airline & Loop & API & Auth --> Store --> ADB
```

模块边界：
- `agent/` 的通用运行时（模型流、chunk 解析、工具分发、取消、OpenTelemetry span）是 Pydantic AI Slim（钉 2.71.0）；出票、
  ReplyGuard、app.db 历史、SSE 契约、重试、请求上下文、预算和并发仍是我们的代码（[ADR-001](adr-001-agent-runtime.md)）。
- `core/` 不知道 LLM、HTTP、app.db，只吃 `Network` 和参数，返回记录或明确的错误类型。可单独穷举测试。
- `airline/`：`find_city`、`quote_route`、`request_booking`（只出确认卡）、`post_assistant_message`（`done` 时展示卡片并签发 nonce）、`book(quote_id, nonce)`（出票）、
  `list_tickets`；内部调 core 和 store，负责出票条件 B1–B7 和权限（只能操作自己的报价和票）。时刻、座位接口位置预留，
  **不实现、不编造数据**（locations.db 里没有这些信息）。
- `agent/` 不直接读 locations.db 或 core，只通过 tools → `airline/`。**agent 没有任何出票路径**。
- 所有 LLM 请求和响应、工具调用和结果都落库：用于「查看历史会话」、溯源和离线评测回放。

## 3. 一轮对话的数据流

### 3.1 询价

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant FE as 前端
  participant L as agent loop
  participant M as LLM
  participant A as airline
  participant S as app.db

  U->>FE: "我想从 Guangzhou 飞到 Lima"
  FE->>L: POST /api/conversations/{id}/messages（SSE）
  L->>S: 存 user message
  L->>M: system + 历史 + 工具定义
  M-->>L: tool_call quote_route(origin="Guangzhou", destination="Lima")
  L->>A: 校验参数 → quote_route
  A->>S: 存 Quote(q_7f3a, status=quoted)
  A-->>L: Quote 记录（航段、中转、距离、价格的显示值 + 高精度值）
  L-->>FE: SSE card：行程卡（按钮暂时禁用）
  L->>M: tool result
  M-->>L: 流式文本
  L->>L: 按句校验：数字 / 城市名都能在记录里找到？
  L-->>FE: SSE delta（逐句）
  L->>S: 存 assistant message + 卡片；quote → presented
  L-->>FE: SSE done → 行程卡按钮启用
```

### 3.2 确认出票

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant FE as 前端
  participant L as agent loop
  participant M as LLM
  participant A as airline
  participant S as app.db

  U->>FE: "好的，就订这个"
  FE->>L: 用户消息
  L->>M: 历史（含 q_7f3a 已展示）
  M-->>L: tool_call request_booking(quote_id="q_7f3a")
  L->>A: request_booking
  A->>S: 检查归属 / 状态 / 有效期（不签发 nonce）
  A-->>L: 确认卡记录（完整航段、中转、距离、价格；工具结果里永远没有 nonce）
  L-->>FE: SSE card：确认卡（按钮禁用）
  L-->>FE: 文本 + done（此刻卡片 presented，nonce 随 done 的消息下发）
  U->>FE: 点击「确认预订」
  FE->>A: POST /api/quotes/q_7f3a/book（nonce）
  A->>S: BEGIN IMMEDIATE：B1–B7
  A->>A: 用当前 locations.db 重算同一路线，与 quote 比对
  alt 全部通过
    A->>S: 写 Ticket（航段快照）+ quote → booked，COMMIT
    A-->>FE: 票卡
    A->>S: 追加一条系统消息到会话（出票结果）
  else 任一不通过
    A->>S: quote → expired / stale（记录原因）
    A-->>FE: 拒绝原因（如数据已变化，请重新报价）
  end
```

用户也可以直接点行程卡上的「确认预订」按钮（行程卡本身就展示了完整路线、距离、价格，展示时同样签发 nonce）。
无论哪条路，**出票的触发都是用户的一次点击**，LLM 只能让确认卡出现。

## 4. Agent loop 设计

### 4.1 状态机

```mermaid
stateDiagram-v2
  [*] --> BuildContext: 收到用户消息（已落库）
  BuildContext --> CallLLM
  CallLLM --> ExecTools: 有 tool_calls
  CallLLM --> StreamReply: 文本
  CallLLM --> Fallback: LLM 错误且重试用尽
  ExecTools --> CallLLM: 结果追加（step < MAX_STEPS）；卡片已推送
  ExecTools --> Fallback: step = MAX_STEPS
  ExecTools --> Fallback: 每轮工具调用用尽且有被拒调用
  ExecTools --> Fallback: 本轮 token ≥ 软上限
  StreamReply --> StreamReply: 一句通过校验 → 推送
  StreamReply --> Fallback: 某句校验失败 → replace
  StreamReply --> Persist: 文本结束
  Fallback --> Persist: 代码模板回复（只用记录）
  Persist --> [*]: 存 assistant 消息 + 卡片；quote → presented；done
```

### 4.2 伪代码

```python
def run_turn(conv, user_text, emit) -> AssistantMessage:
    with conversation_lock(conv):                  # 同会话串行；出票接口也拿同一把锁
        store.add_message(conv, role="user", text=user_text)
        ctx = build_context(conv)                  # system + 历史（LLM 原文）+ 历史工具结果（精简）
        produced = []                              # 本轮记录：quote / booking request / ticket list / city
        for step in range(MAX_STEPS):              # MAX_STEPS = 6
            stream = llm.chat_stream(ctx, tools=TOOL_SCHEMAS, temperature=0)
            if stream.tool_calls():                # 分片的参数拼完整后再执行
                for call in stream.tool_calls():
                    emit("status", status_text(call))
                    result = dispatch(call, conv)  # pydantic 校验 → airline → ToolCall 落库
                    produced += result.records     # 校验失败也返回结构化错误，让 LLM 自行修正
                    for card in cards_for(result.records):
                        emit("card", card)         # 卡片立即推送，按钮在 done 前禁用
                    ctx.append(tool_message(call.id, result.for_llm()))
                store.add_llm_call(conv, ctx, stream.raw())
                continue
            guard = ReplyGuard(records=visible_records(conv, produced), user_text=user_text)
            for sentence in stream.sentences():    # 按句缓冲
                ok, problems = guard.check(sentence)
                if not ok:
                    emit("replace", fallback_reply(produced))
                    return persist(conv, fallback_reply(produced), produced, guard_problems=problems)
                emit("delta", sentence)
            store.add_llm_call(conv, ctx, stream.raw())
            return persist(conv, stream.text(), produced)
        emit("replace", fallback_reply(produced))
        return persist(conv, fallback_reply(produced), produced)
```

### 4.3 上下文
- system prompt：角色；工具使用规则；「事实在卡片里，文字里不要自己算数，提到数字只能照抄工具结果的显示值」；
  「用户要订票时调用 `request_booking`，告诉用户点击确认卡出票」；「城市名有歧义时先问用户」；当前日期。
  **不放城市列表**（城市由工具解析）。
- 历史：本会话全部消息，**用 LLM 原文**（与渲染后一致，因为文本不再做替换）；工具结果以精简 JSON 保留
  （记录 id + 显示值），超过预算时从最早的轮次开始压缩成摘要，quote 和 ticket id 始终保留。
- LLM 参数：`temperature=0`，`tool_choice=auto`；并行工具调用允许（按顺序执行）。由 Pydantic AI 的 `OpenAIChatModel`（DeepSeek
  方言）发出；`max_tokens` 同 §4.8。

### 4.4 工具

| 工具 | 参数 | 返回的记录 | 为什么这样设计 |
|---|---|---|---|
| `find_city` | `name` | `CityRecord`：规范名、是否有服务、是否 hub、直连 hub；无服务时附最近的有服务城市（前 3 个）；找不到或有歧义时附候选 | 城市解析和「无服务 → 最近有服务城市」（要求 8）是确定性逻辑；歧义（如 `San Jose` / `San José`）返回 `ambiguous_city`，由用户选 |
| `quote_route` | `origin`, `destination` | `Quote`：航段、中转、距离、价格（显示值 + 高精度值）、来源、库指纹、有效期；错误：`unknown_city` / `ambiguous_city` / `same_city` / `no_service`（附最近有服务城市，排除另一端）/ `unreachable` | 询价产生不可变记录；确认卡、出票都引用同一个 id |
| `request_booking` | `quote_id` | `BookingRequest`：确认卡（完整航段、中转、距离、价格、有效期）；nonce 在本轮 `done` 时随消息下发，不进工具结果 | LLM 只能让确认卡出现，不能出票；出票的触发是用户点击（P4） |
| `list_my_tickets` | 可选：`city`、`ticket_id`、`booked_after/before` | `TicketList`：票据记录 + `count` | 要求 9：票据只从 app.db 读；数量由代码给出（FSR 的计数教训） |

工具设计取舍：
- **少而粗**：4 个工具覆盖全部需求。工具越少，LLM 选错的机会越少，也更容易统计成功率。
- 参数只收**名字和 id**，不收距离、价格等数字，LLM 没有机会把自己算的数传进来。
- 返回给 LLM 的都是已格式化的显示值，LLM 不需要也不应该自己舍入或换算。
- 错误也是结构化记录，LLM 据此组织语言，但事实来自记录；**无服务、歧义、找不到也各有一张卡片**，
  即使 LLM 文字写错，卡片上的事实是对的。

### 4.5 回复校验（ReplyGuard）
- **数字按值溯源**：句子里每个数字（含金额、公里数、计数）规范化后必须等于本轮可见记录的某个显示值
  （如 `$1,234.57`、`9,497.3 km`、`3`）。另外允许：用户本条消息里原样出现过的数字、Markdown 有序列表序号、
  记录 id、日期。
- **城市名按值溯源**：句子里出现的任何库内城市名，必须出现在本轮记录里或用户本条消息里（防止 LLM 用常识推荐
  「附近的 Dubai」——库里 Abu Dhabi 最近的有服务城市是 Tirana）。
- 失败：流式下已推出的句子无法撤回，所以**不重写**，直接 `replace` 成代码模板回复（模板只用记录拼句子）。
  失败原因落库，计入评测指标。
- 已知漏洞：文字写出的数（「两千多公里」「three tickets」）看不出来 → prompt 禁止 + 评测检查 + 写入已知限制。
- 03 开工第一步：用约 20 个用例在 DeepSeek flash 上实测校验通过率和回退率，再定 prompt；如回退率过高，改为
  「文字不写任何数字，事实全在卡片」。

- **实现细化（T3，2026-11-23）**：
  - 可见记录 = 上下文里全部工具结果 + 出票事件（不只本轮）；工具只回显 LLM 自己参数的内容（query、filter、错误信息）
    不算记录；
  - 城市名：用户在本会话任何一条消息里提到的库内城市都可出现；prompt 要求城市名一律用记录里的英文原名
    （回复是中文也一样）；被翻译成其他文字的城市名 guard 查不到，写入已知限制；
  - 数字带单位核对（`$` ↔ 价格、`km` ↔ 距离；英里、小时、百分比等一律不放行）；计数只来自代码给的
    `count` / `leg_count` / `transfer_count`；有序列表序号只认 1、2、3… 连续序列；
  - **出票声明规则**：本轮记录里没有用户点击出票或非空的查票结果时，「已出票 / booked / confirmed」类说法回退模板；
    `request_booking` 成功的轮次由代码在末尾追加固定的点击提示；
  - LLM 不可用且本轮没有任何产出 → SSE `error`，只保留用户消息；`finish_reason` 不是 `stop` → 模板；
  - 整轮总时限 90 s、会话锁等待 10 s（超时 `conversation_busy`，消息不保存，额度退还）；内联 `<think>` 块剥离；
    给 LLM 的高精度值去掉，只给显示值。
  - 真模型实测（deepseek-flash，effort high，31 场景 / 50 轮）：50/50 原样放行，0 回退，0 虚假出票声明；费用约 $0.016。

### 4.6 失败处理
| 情况 | 处理 |
|---|---|
| LLM 超时 / 5xx | 指数退避重试 2 次 → 回复「服务暂时不可用」，用户消息保留，可重发 |
| 工具参数不合法 | 结构化错误回给 LLM，计入工具调用成功率的失败 |
| 超过 MAX_STEPS | 模板回复，附本轮已产生的记录 |
| 用户在回复完成前又发消息 / 点按钮 | 同会话串行（每会话一把锁，聊天和出票接口共用）；uvicorn 单 worker |
| 超出限额 | 明确提示（每用户每日消息上限、全站每日预算） |
| 本轮工具调用或 token 超出预算 | 超出的工具调用不执行并回结构化拒绝；需要结束时模板回复（`tool_budget` / `token_budget`），见 §4.8 |
| 全站对话轮次已满 | 最多等 5 s；仍满 → `server_busy`，消息不保存，额度退还，前端保留输入 |
| app.db 锁等待 / 操作超时 | 回滚；HTTP 503 `data_unavailable`（出票可用同一 nonce 重试）；对话里走模板 / `internal_error` |
| 同一用户已有 2 个轮次在跑 | `conversation_busy`（其他会话忙），什么都不存，额度退还 |
| 用户消息没能存入 app.db | `internal_error`（没有保存），额度退还 |

### 4.7 流式输出
- 传输：`POST /api/conversations/{id}/messages` 返回 SSE（`text/event-stream`），前端用 fetch 逐段读取。
- 事件：
  | 事件 | 内容 | 时机 |
  |---|---|---|
  | `status` | 「正在查询航线…」等进度 | 每次工具调用前 |
  | `card` | 行程卡 / 确认卡 / 票卡 / 无服务卡 / 歧义卡（完全由记录渲染） | 工具返回后立即推 |
  | `delta` | 通过校验的一句文本 | LLM 流式输出时，按句 |
  | `replace` | 整条文本替换为代码模板 | 某句校验失败 |
  | `ping` | 心跳 | 每 15 秒（防止 Cloudflare 断开空闲连接） |
  | `done` / `error` | 消息 id、各卡片的服务端状态 | 结束 |
  | `cancelled` | 停止后存下的回复（或 `null`：用户消息还没存） | 用户按 Stop 后的终止事件 |
- 卡片按钮在 `done` 之前禁用；之后的按钮状态（可订 / 已出票 / 已过期）一律以服务端当前 quote 状态为准，
  包括断线重连后拉到的历史卡片。
- 断线：后端照常跑完一轮并落库，前端重连后拉会话即可看到完整结果。

### 4.8 每轮预算（06 P0a，#32）
集中在 `agent/budget.py`：`AgentBudget`（配置）+ `BudgetTracker`（一轮的计数）。loop 只在两处问它：每次调 LLM 前
（`stop_before_step`）和每个工具调用前（`admit_tool_call`）。

| 预算 | 默认 | 环境变量（范围） | 超出时 |
|---|---|---|---|
| LLM 步数 | 6 | `AGENT_MAX_STEPS`（1–20） | 模板回复，`max_steps` |
| 每步工具调用 | 4 | `AGENT_MAX_TOOL_CALLS_PER_STEP`（1–16） | 多出的不执行，回结构化拒绝，本轮继续 |
| 每轮工具调用 | 12 | `AGENT_MAX_TOOL_CALLS_PER_TURN`（1–64） | 多出的不执行，回结构化拒绝，本步结束后模板回复，`tool_budget` |
| 单次输出 token（含思考） | 4096 | `AGENT_MAX_OUTPUT_TOKENS_PER_CALL`（256–32768） | 请求的 `max_tokens`；截断 → 模板（`truncated`） |
| 每轮总 token（软上限） | 60000（原 20000；06 P1 实测长会话中三步轮次约 27k） | `AGENT_MAX_TOTAL_TOKENS_PER_TURN`（1000–1000000） | 不再开始下一步，模板回复，`token_budget` |
| 每轮总时长 | 90 s | `AGENT_MAX_TURN_SECONDS`（5–600） | 模板回复（`turn_timeout`）；没有产出 → `llm_unavailable` |

环境变量不合法（非数字、越界、非整数）时应用启动失败，不会带着错误的预算运行。

- **工具调用逐个计数**，不是按 LLM 步计数。一次 completion 里超出的调用**不执行**（不发 `status`、不碰 airline），
  每个都得到一条 `{"code":"tool_budget_exceeded","message":…,"limit":"per_step"|"per_turn"}` 工具结果，
  所以每个 `tool_call_id` 都有回应，协议有效；拒绝也像普通调用一样存入 `tool_calls`（`error = "budget: …"`），
  下一轮重建的上下文与记录的 completion 对齐。拒绝不是记录：不进 ReplyGuard，模板也不引用它。
- 一次 completion 的工具调用超过 `每步上限 × 4`（默认 16）时视为异常：一个都不执行、不逐条落拒绝，本轮以模板结束
  （`tool_budget`，值为调用数）；该 completion 在 `llm_calls` 里标 `tool_calls_not_run`，重建上下文时跳过它。
- 工具返回后，结果、卡片和给 LLM 的消息立即登记到本轮，再写 `tool_calls` 行；时限落在写行上时，模板回复仍带这张卡。
- **token 软上限**：在两次 LLM 请求之间、以及每次重试之前检查（正在进行的那一步会跑完，超出后不再开始下一步，也不再重试）。
  每次调用按以下规则计费：服务商回报的用量取 `max(total_tokens, prompt_tokens + completion_tokens)`（0 不算回报）；
  **没读到任何响应体的失败**（HTTP 错误状态、连接 / 握手失败）计 0；其余没有可用回报的调用（流中途断开、
  completion 没带 usage）按估算计：`ceil(请求 JSON 字符数 / 2) + ceil(已收到的输出字符数 / 2)`（输出含文本、思考、
  工具调用，最多到单次输出上限）；输出量不可知时（本次调用超时、ReplyGuard 截断、整轮时限打断）按整个单次输出上限计。
  整轮时限打断的调用也落一条 `llm_calls`（`aborted: "turn_timeout"`）。每次计费写在 `llm_calls.response.budget`；
  `llm_calls.tokens` 存同一个计费值（服务商原始回报仍在 `response.usage`），全站每日预算（§8.2）按它累计，
  所以两者口径一致。**这不是严格的费用上限**：单次调用最多可以超出剩余额度一整步，价格也按模型而异。
- **预算停止永远不出票、不改 quote 状态**：agent 本来就没有出票路径；被拒的 `request_booking` 不执行，所以不会出现确认卡。
  已执行的工具结果和卡片照常保留，assistant 消息照常存储（模板），会话保持良构。
- 停止时记一条 warning 日志（步数、已执行/被拒调用数、token、估计次数）。

### 4.9 并发与阻塞调用（06 P0b，#33）
- **全站并发上限**：同时运行的对话轮次最多 `AGENT_MAX_ACTIVE_TURNS`（默认 8，1–256），在每会话锁之外。先拿会话锁，再拿名额
  （先来先得），最多等 `AGENT_TURN_QUEUE_SECONDS`（默认 5 s，0–60）；等不到 → SSE `error server_busy`，在 `user_message`
  之前发出，什么都不存，并退还当天的消息额度。等待是 `await`，不阻塞事件循环。
- **线程不能强制中止**：工具和 app.db 调用在工作线程里跑（`asyncio.to_thread`）；超时只能停止等待，线程会跑完。所以本轮
  在时限内启动的线程都登记下来；整轮超时或出错后，先等它们结束（最多 30 s）再落 assistant 消息、释放会话锁和名额——
  否则线程可能在锁释放后还在写 quote。超过 30 s 记 error 日志后照常结束。线程占着名额，因此线程数有上限。
- **app.db 时限**：每条语句等锁最多 10 s（busy timeout），每个连接（一次读或写事务）从打开起 10 s 后中断正在跑的 SQL；
  两者都回滚并抛 `StoreUnavailable`。HTTP 一律 503 `data_unavailable`（出票：没写入任何东西，quote 和 nonce 不变，可重试）；
  对话轮次里走现有的 `internal_error` / 模板回退。
- **每用户上限**：同一用户同时运行的轮次最多 `AGENT_MAX_TURNS_PER_USER`（默认 2，1–256），在会话锁之后、名额之前检查；
  超出 → SSE `error conversation_busy`（文案说明是其他会话在忙），什么都不存，额度退还。
- **额度口径（#55）**：每日消息上限数的是**存下来的用户消息**。凡是什么都没存、也没开始 LLM / 工具工作就被拒的轮次
  （同会话锁等待超时、每用户上限、`server_busy`、排队时被 Stop、用户消息没存上、文本不合法），都退还它自己那一条预留，
  只退一次（按预留 id 删除；退还开始后 Stop 已经晚了，返回 `finishing`）。预留仍在开流之前原子完成，名额和锁的获取顺序不变。
  之前 `conversation_busy` 不退额度（按「放进来的尝试」计），而界面说「消息没有发送」，两者矛盾；改后 `/api/me` 的
  `messages_today` 等于当天存下的用户消息数。测试：`backend/tests/test_busy_quota.py`（HTTP 层，查计数、预留行和消息）。
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


### 4.10 运行时（ADR-001，#49）
每轮一个 Pydantic AI run：`Agent.iter(message_history=…)`，loop 在 `ModelRequestNode` 上流式读事件（文本 → SentenceBuffer →
ReplyGuard → `delta`），在 `CallToolsNode` 上执行工具。§4.2 的伪代码不变。
- 工具：`Tool.from_schema(TOOL_SCHEMAS)`，串行、按调用顺序，`strict` 关；执行体是 `Toolbox.run`，用户和会话来自 run 的 `deps`，
  阻塞部分走 `Threads`。参数不是 JSON 对象时原串交给 `Toolbox.run`（`invalid_arguments`）；未知工具名由 loop 记为 `unknown_tool`；
  同一响应里重复的 `tool_call_id` 改成 `id~2`… 后照常执行。
- 请求：每次请求的消息都是 loop 自己的上下文（`before_model_request`：app.db 历史 + 本轮步骤，工具调用为 wire-safe 形式，会话中途的
  system 消息保持 system 角色）；thinking 字段经 `extra_body` 发出，与迁移前相同；不加 SDK 的说明文字和 openai 客户端指纹头。
  唯一保留的 SDK 附加：用户文字里出现仿冒 `<system>` 的标签时，SDK 在后面加一条说明（只到模型）。
- 重试：provider SDK 自身不重试（`max_retries=0`）；loop 在一步还没放出文字时按 §4.6 重试，每一步各有 2 次，每次尝试都记 `llm_calls`
  并计费，从上下文重开 run。模型流里的任何异常都算一次失败的调用；模型调用之外的异常仍是 `internal_error`。
- 结束原因：读服务商原始 `finish_reason`；没有时当作断流（可重试），不当作 `stop`。
- 取消（#48）：只有 Stop 接口（`POST /api/conversations/{id}/runs/{run_id}/cancel`）能停止一轮；断开 SSE 不算（照常跑完落库）。
  每轮有 app 自己的 `run_id`（响应头 `X-Run-Id` + 第一个事件），进程内登记（单 worker；多 worker 需要共享的取消标志或按会话粘性路由）。
  状态 `queued → running → finishing → completed / failed`，或 `→ cancelling → cancelled`，终态由终止事件决定（R4）。
  排队时 Stop：什么都不存、退还当天额度。之后的 Stop：不再发起 LLM 请求、重试和工具调用，模型流立即关闭；已开始的工具线程跑完、
  drain 后，只存已通过 ReplyGuard 的文字和已完成工具的卡片（`status = cancelled`，卡片不 presented、无 nonce），没结果的工具调用存为
  `cancelled` 行（下一轮上下文里是 `{"code":"cancelled"}` + 一条 system 说明）。Stop 在落库阶段或之后到达不改变任何东西。
  将来可选模式：`after_turn`（当前步结束后停）。规则 R1–R8 见 state_invariants.md。
- Tracing：默认不装 instrumentation、无 exporter、无 logfire；`Agent(tracer_provider=…)` 给 #35 用（不带消息内容）。

## 5. 路线、距离、价格

- 图：hub–hub 边来自 `hub_links`，非 hub–hub 边来自 `connected_hubs`；双向。非 hub 城市只能当端点（只在 hub 换乘）。
- 价格和距离成正比 ⇒ 最便宜 = 最短；欧氏距离满足三角不等式 ⇒ 有直飞时直飞最便宜（要求 5 自然满足）。
- **规范计价函数**（全系统唯一，报价、出票复核、参考比对都只用它）：给定城市序列，在固定的 Decimal 上下文
  （prec=50, ROUND_HALF_EVEN）里按航段顺序计算 `sqrt(dx²+dy²)`、求和、乘 `Decimal("0.13")`，量化为 30 位小数的字符串。
  坐标是整数，只有完全平方数的航段距离是精确值，其余是 50 位精度近似；文档和界面都称「高精度值」。
- 选路：加载（或库指纹变化）时，对每个 hub 对用 networkx `shortest_simple_paths`（Yen）取出「整数边权最优 + 很小容差」
  内的全部简单路径作为候选（边权 = 规范单段距离量化到 1e-30 后的整数，整数运算无误差；容差只会多收候选，
  保证 √2+√8 对 √18 这类无理数平手不被漏掉）。查询时枚举「起点直连 hub × 终点直连 hub」拼出候选路线，全部交给规范函数
  计价，按（价格，航段数，城市名序列）取最小。**networkx 只用来产生候选，不使用它返回的长度。**
  实测：真实库每个 hub 对恰好 1 条候选，加载约 0.12 s；全部 490,700 个有服务城市对与参考实现比对一致，用时约 28 s。
- 存储值上「各段之和」与「总距离」可能相差 ≤ 航段数 × 1e-30（先对未舍入值求和再量化一次，避免把舍入值喂回计算）；
  「计算依据」以总距离字段为准。
- 显示：距离 1 位小数（km），价格到分（ROUND_HALF_UP）；舍入只在渲染层。「计算依据」显示每段高精度距离、求和、
  × 0.13，并注明「价格按未舍入的距离计算，所以显示距离 × 0.13 可能和显示价格差 1 分」。
- 城市解析：先按原样（NFC）精确匹配；不中再做大小写 + 去重音折叠匹配，折叠后命中多个（库里唯一的一组是
  `San Jose` 与 `San José`）→ `ambiguous_city`，绝不自动选；仍不中 → difflib 候选。命中的城市如有折叠同名的兄弟，
  记录里带 `similar_names`，卡片显示区分信息（直连 hub）。
- 最近有服务城市：在有服务城市中按距离取前 3 个，可排除指定城市（避免建议的城市就是另一端）。
- 正确性保证：
  1. 独立参考实现（逐对 Bellman-Ford，不共享代码）与生产实现在**全部有服务城市对**上比对路线成本；
  2. hypothesis 随机图（不连通、平手、共线）；
  3. 结构性检查：中间城市都是 hub、每段都是真实航线、总距离 = 各段之和、价格 = 距离 × 0.13；
  4. 线程一致性：默认上下文的新线程里算出的报价字符串与主线程逐字节相同；
  5. 呈现侧：卡片只由记录渲染；文本经 ReplyGuard（§4.5）；评测再查一遍「回复中每个数字、城市都能在记录里找到」。

### 5.1 溯源记录（Quote 示例）

```json
{
  "id": "q_7f3a",
  "origin": "Guangzhou", "destination": "Lima",
  "legs": [
    {"from": "Guangzhou", "to": "Johannesburg",
     "from_coord": [-9278, 3445], "to_coord": [...],
     "source": "locations.connected_hubs(Guangzhou)",
     "distance": "…(30 位小数)…"},
    {"from": "Johannesburg", "to": "…", "source": "hub_links(…,…)", "distance": "…"}
  ],
  "transfers": ["Johannesburg", "…"],
  "distance": "Σ legs",
  "price": "distance × 0.13",
  "display": {"distance": "…km", "price": "$…"},
  "formula": "price = Σ sqrt(dx²+dy²) × 0.13 USD; Decimal prec 50, stored to 30 dp",
  "network_fingerprint": "sha256:…",
  "algorithm": "hub all-pairs (networkx) + endpoint enumeration; tie-break: legs, names",
  "status": "quoted | presented | booked | expired | stale",
  "created_at": "…", "expires_at": "+15 min"
}
```
（示例中的城市和数字仅示意，非真实结果。）

## 6. 确认与出票（要求 7）

```mermaid
stateDiagram-v2
  [*] --> quoted: quote_route
  quoted --> presented: 卡片随 done 发给用户
  presented --> booked: 用户点击确认 且 B1–B7 通过
  presented --> expired: 超过 15 分钟
  presented --> stale: 出票复核发现数据变化
  booked --> [*]
  expired --> [*]
  stale --> [*]
```

没有「取代」：同一会话可以同时存在多张有效报价（比较两条航线、往返各一张都正常）。

出票条件（全部满足才出票；编号规则在 `docs/design/state_invariants.md`，用模型测试覆盖）：
- **B1 归属**：quote 属于当前登录用户。
- **B2 已展示**：quote 状态为 `presented`，即带完整航段、中转、距离、价格的卡片已随 `done` 发给用户。
- **B3 用户点击**：请求来自出票接口，携带该卡片签发的一次性 nonce（nonce 绑定 quote、用户、卡片所在消息；用一次即失效）。
  LLM 没有调用出票接口的路径。
- **B4 有效期**：未超过 15 分钟。
- **B5 幂等**：同一 quote 最多一张票（`tickets.quote_id` UNIQUE）。
- **B6 复核**：用当前 locations.db（指纹检查，必要时重载）重算同一城市序列：每段两端城市和坐标与 quote 一致、
  该航段仍存在，规范函数算出的距离和价格与存档字符串相等；起终点的最便宜路线仍是这条。否则 quote → `stale`，
  提示重新报价，用户需重新确认。
- **B7 原子性**：`BEGIN IMMEDIATE` 事务内完成检查、写票、改 quote 状态；聊天和出票接口共用会话锁；uvicorn 单 worker。
- **Stop（#48，R6）**：被用户停止的轮次不产生出票权限：它的 quote / 确认卡随 `cancelled` 消息存储但不 presented（无 nonce、不可点），
  quote 保持 `quoted` 直到过期；用户需要在新的轮次里重新询价或请求确认。Stop 不出票，也不撤销已出的票或已展示的报价。

如何回答 take-home 的「怎么保证未经确认不出票」：出票只有一个接口，只接受用户点击带来的 nonce，而 nonce 只在
完整路线 / 距离 / 价格的卡片发给用户之后才签发；LLM 能做的只是让这张卡片出现。自然语言里的「好的，订吧」
会让确认卡出现，但不会出票。

## 7. 数据模型（app.db）

```mermaid
erDiagram
  users ||--o{ conversations : has
  conversations ||--o{ messages : has
  conversations ||--o{ llm_calls : has
  messages ||--o{ tool_calls : triggers
  conversations ||--o{ quotes : has
  quotes ||--o| tickets : "booked as"
  users ||--o{ tickets : owns

  users {
    text id PK
    text auth_provider
    text provider_sub UK
    text email
    text display_name
    text created_at
  }
  conversations {
    text id PK
    text user_id FK
    text title
    text created_at
    text updated_at
  }
  messages {
    text id PK
    text conversation_id FK
    text role
    text text
    json cards
    json guard_problems
    text created_at
  }
  llm_calls {
    text id PK
    text conversation_id FK
    json request
    json response
    int latency_ms
    int tokens
    text model
  }
  tool_calls {
    text id PK
    text message_id FK
    text name
    json args
    json result
    text error
    int latency_ms
  }
  quotes {
    text id PK
    text conversation_id FK
    text user_id FK
    json legs
    text distance
    text price
    text network_fingerprint
    text status
    text expires_at
  }
  quote_cards {
    text quote_id FK
    text message_id FK
    text presented_at
    text nonce_used_at
  }
  tickets {
    text id PK
    text user_id FK
    text quote_id UK
    json legs_snapshot
    text distance
    text price
    text network_fingerprint
    text booked_at
  }
```

- 距离、价格存成高精度十进制字符串（规范函数的输出），显示值不入库参与计算。
- nonce 不入库：`HMAC(secret, quote_id ␟ user_id ␟ message_id)` 现算（刷新页面后同一张卡仍可得到有效 nonce）；
  `quote_cards` 记录卡片出现在哪条消息、何时展示、nonce 是否已用（一次性）。`NONCE_SECRET` 未设置时每个进程随机，
  重启后打开的页面需要刷新。
- `locations.db` 必须是回滚日志模式且没有 `-wal` 文件（WAL 中的已提交修改不会改变主文件哈希）；部署时用原子替换
  （写新文件再 rename）。读不到时出票返回 `data_unavailable`（可重试），不把报价判为 stale。
- `app.db` 在 `data/` 目录，不进 git（含用户邮箱、全部对话）。
- 用户：`(auth_provider, provider_sub)` 唯一；固定账号是 `auth_provider = "dev"`。

v4（#48）：`messages.status`（`completed` | `cancelled`，旧行默认 `completed`）。迁移只向前；旧版本镜像遇到更新的 schema 拒绝启动，
回滚步骤见 deploy.md（先备份，或 `DROP COLUMN status` + `user_version = 3`）。

## 8. API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/dev` | 固定账号登录（环境变量开关；本地 / 开发） |
| POST | `/api/auth/google` | 提交 Google ID token，后端核验后发会话 cookie（05） |
| POST | `/api/auth/logout` | 退出 |
| GET | `/api/me` | 当前用户 |
| GET | `/api/config` | 前端运行时配置（如 Google client ID，避免构建时写死） |
| GET | `/api/conversations` | 会话列表（要求 10） |
| POST | `/api/conversations` | 新会话 |
| GET | `/api/conversations/{id}` | 会话全部消息 + 卡片（卡片状态以服务端为准） |
| POST | `/api/conversations/{id}/messages` | 发消息，SSE 流式返回（§4.7） |
| POST | `/api/quotes/{id}/book` | 用户点击确认出票（带 nonce；B1–B7） |
| GET | `/api/quotes/{id}/trace` | 计算依据 |
| GET | `/api/tickets` | 我的机票（要求 9） |

**所有带 id 的读写接口都检查归属**（不是自己的返回 404），不只是出票。

### 8.1 登录
- **分两步**：开发期和本地运行用固定账号（`DEV_AUTH_USER` / `DEV_AUTH_PASSWORD`，来自 `.env`，环境变量开关）；
  数据从第一天起就按多用户设计。demo 公开前（05）接入 Google 登录并走通多用户流程。
- Google 登录：前端用 Google Identity Services 拿 **ID token**（不需要 client secret，只要 `openid email profile`）；
  后端用 Google JWKS 验签，检查 `aud`、`iss`、`exp`、`email_verified`，通过后发**自己的** httpOnly 会话 cookie
  （`Secure` 按环境开关：Caddy 后面是 http，经 Cloudflare 对外是 https）。依赖在 05 开工时选（`google-auth` 或
  `PyJWT[crypto]`）并写明理由。设计文档要写清楚这个第三方登录怎么工作（take-home 非必须项第 2 条）。
- 评审：OAuth 应用必须发布为 Production（Testing 状态只有名单内用户能登录）；另给评审一个不依赖 Google 的入口
  （邀请码登录，带限额）。
- Google 只接受 `http://localhost` 或 https 域名作为 JavaScript 来源；用局域网 IP 访问时只能用固定账号。

### 8.2 限额（公开 demo 前必须有）
- 每用户每日消息数上限（数存下来的用户消息，被拒的忙碌轮次退还，§4.9）、全站每日 token / 费用预算（按 `llm_calls.tokens` 累计），到顶后明确提示；
  LLM 服务商侧也设消费上限。

### 8.3 部署与交付
```mermaid
flowchart LR
  User["浏览器"] -->|HTTPS| CF["Cloudflare<br/>ocean-above.potafree.net"]
  CF -->|Tunnel| Proxy
  subgraph Compose["docker compose（homelab，将来可换 VPS）"]
    Proxy["proxy · Caddy<br/>唯一对外端口（loopback）"]
    Web["web · Next.js :3000"]
    Api["api · FastAPI :8000（单 worker）"]
    Proxy -->|"/api/* (SSE 不缓冲)"| Api
    Proxy -->|"其余"| Web
  end
  Api --- Vol[("./data/app.db")]
  Api --- LDB[("./locations/ 目录只读挂载")]
```
- 交付物：本地 `docker compose up` 后打开 `http://localhost:3000`；demo 站点用同一份 compose（`OCEAN_PORT=18765`）。
- 评审本地运行需要一个 OpenAI 兼容的 LLM key（README 写明怎么配）；或直接用线上 demo。
- 同源：前端只请求相对路径 `/api/*`；开发时 Next.js 把 `/api` 转给本地后端。
- `locations.db` 所在**目录**只读挂载（单文件挂载在宿主机替换文件后容器看不到新文件，指纹重载会失效）。
- 前端配置运行时从 `/api/config` 取，同一个镜像可用于本地和 demo。
- 日志 stdout/stderr，json-file 轮转 10m × 3；`restart: unless-stopped`。细节见 `docs/deploy.md`。

## 9. 前端

- 页面：登录、聊天（会话列表 + 对话 + 卡片）、我的机票、历史会话只读查看。
- shadcn/ui + Tailwind；移动端：会话列表变抽屉、卡片单列、触控目标 ≥ 44px、无横向滚动。
- Markdown 流式渲染：优先 Vercel `streamdown`，开工前核实体积，只启用需要的部分；不合适则 `react-markdown` + 按块 memo。
- 卡片是独立组件，不经过 Markdown：行程卡（航段、每段距离、总距离、价格、有效期倒计时、「确认预订」、可展开「计算依据」）、
  确认卡、票卡、无服务卡（「X 暂无航班，最近的有服务城市是 …」）、歧义卡（列出候选及区分信息）。
- 审查：Impeccable（critique / audit / detect）+ shadcn 对应 skill；ego-browser 桌面和手机截图。

## 10. 测试与评测

| 层 | 内容 | 何时跑 |
|---|---|---|
| core 单元 + 属性测试 | 规范计价、舍入、路线规则、城市解析（含 San Jose / San José）、参考实现全对比对、随机图、线程一致性 | 每次 |
| 出票不变式模型测试 | B1–B7：随机事件序列（报价、展示、用户消息、LLM 请求下单、点击、重复点击、并发点击、时钟推进、改库）每步检查 | 每次 |
| agent 回放测试 | 脚本化假 LLM：loop、分发、ReplyGuard、回退、SSE 事件顺序、LLM 无出票路径 | 每次 |
| API 测试 | httpx TestClient：完整流程、归属检查、限额 | 每次 |
| LLM 评测 `backend/evals/` | 工具调用成功率（每用例 N 次，按工具统计，有阈值）；整段轨迹（工具序列 + 不变式：否定 / 疑问不请求下单、数字和城市都有出处、无服务给库内最近城市（选库内最近 ≠ 地理常识的城市：Abu Dhabi、Stuttgart、Austin）、歧义城市先问、查票走工具）；ReplyGuard 回退率 | 按需；单次估算 < $1 直接跑 |
| UI | ego-browser 桌面 + 手机截图；Impeccable | 04 起每个 UI 改动 |

## 11. 已知限制与下一步（初稿）

- 无航班日期、时刻、座位、乘客信息、支付、退改签（take-home 未要求；写进「真实上线还需要什么」）。
- 出票需要一次点击：自然语言「订吧」只会弹出确认卡。这是为了让「未经确认不出票」由代码保证，而不是由 LLM 判断。
- ReplyGuard 拦不住用文字写出的数；可能误伤用户提到的其他数字 → 白名单 + 模板回退，回退率在评测里统计。
- 单进程、单 worker、SQLite；并发量有限，demo 足够。WAL 单写者、按会话的进程内锁、`_recheck` 在写事务内、基准实测和迁 PostgreSQL 的判断条件见 [sqlite-concurrency.md](sqlite-concurrency.md)（#41，实测：完整性 0 失败，单 worker 的 CPU 先饱和，约 200 req/s）。
- 航班时刻、座位、库存：接口位置预留在 `airline/`，现在不实现、不编造。
- Bonus（必交物之后）：thinking level 作为用户高级选项（默认 high）；BYOK（用户自带 key，只在浏览器保存、按请求发送，
  服务端不存不记）；默认 key 由网关层限流；用评测框架 bench DeepSeek flash 各 thinking level 的通过率与成本，
  选最划算的默认值。
- 下一步候选：把 `airline/` 拆成 MCP server（权限、真实订票系统对接）、多乘客、日期和库存、多语言、支付、
  管理端改航线后的通知。

## 12. 决定记录
- 2026-11-23 v0 review：FSR 思路 ✔；中文草稿，最终出英文版 ✔。
- 2026-11-23 v1：流式输出提前到 03；`streamdown` 优先；Google 登录在 demo 公开前做；不上 MCP，保留 `airline/` 边界；
  demo 站点 `ocean-above.potafree.net`（homelab + Cloudflare Tunnel，docker compose 交付，可迁 VPS），部署 demo 不用问；
  开发期用固定账号。
- 2026-11-23 v2（设计评审后，用户确认全部推荐）：
  - 出票只由用户点击触发，LLM 用 `request_booking` 弹出确认卡（取代「自然语言直接出票」和 `user_confirmation` 原话核对）；
  - 回复校验改为按值溯源（数字 + 城市名），不再要求 LLM 写 `{{id.field}}` 引用；流式下失败直接换模板；
  - 城市名歧义显式处理；取消报价「取代」；规范计价函数；hub 全对最短路 + 端点枚举；
  - 迭代顺序：03 结束时有本地可运行的端到端版本，README / 设计文档增量写；Google 登录、限额在 demo 公开前；
  - 会话导出脚本现在就加，每个迭代结束导出一次。
