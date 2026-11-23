# 01-architecture — 模块拆分、整体设计和迭代路线

User request (2026-11-23, verbatim): 「按 pfstack 开始：先写计划给我确认」（前文：「完成之后我们开始做模块拆分和设计」）

## Goal
定下整体架构、模块边界、关键算法和确认机制，切成可独立验收的迭代；本文件确认后从迭代 02 开工。

## 数据事实（已核对 locations.db，2026-11-23）
- 711 城市，40 hub，151 条 hub_links；hub 图连通（1 个连通分量）。
- 10 个非 hub 城市无服务（如 Abu Dhabi、Stuttgart、Austin）。有非 ASCII 名（Bogotá、Tromsø…）。
- 坐标全是整数，范围 x∈[-9975, 9997]、y∈[-4992, 5000] km；无脏数据（引用全部合法、无重复、无自环）。
- 距离是欧氏距离 ⇒ 满足三角不等式 ⇒ **有直飞时直飞一定最便宜**；价格与距离线性 ⇒ 最便宜 = 最短距离。

## 架构

```
Browser (Next.js, TS)                      FastAPI (Python)
  chat / tickets / history  ── JSON ──▶   api/        HTTP 路由、简易用户（cookie 里的用户名）
                                          agent/      自写 agent loop：llm client、tools、prompt、确认闸门
                                          core/       纯函数、无 IO 依赖：network、routing、pricing、places
                                          store/      app.db（SQLite）：users、conversations、messages、
                                                      tool_calls、quotes、tickets
                              locations.db（只读，唯一事实来源）   OpenAI 兼容 LLM（DeepSeek flash）
```

数据流（一次对话轮）：用户消息落库 → agent loop 带历史调 LLM → LLM 发 tool call → 后端执行确定性代码 →
工具结果（含结构化卡片）落库并回给 LLM → 循环至 LLM 给出文本 → 返回「文本 + 卡片」给前端。
**价格/距离/航段只从工具结果渲染成卡片**；LLM 文本里的数字由测试核对必须出现在工具结果中。

### core/（确定性，迭代 02）
- `network`：启动时只读加载 locations.db → 不可变 `Network`（城市、坐标、hub 集合、邻接表）。
  不缓存到别处，换库即生效（重启）。
- `routing.cheapest_route(origin, dest)`：只允许在 hub 中转。实现：**networkx Dijkstra**，在「hub 图 +
  起点/终点各自的直连边」上搜（非 hub 城市只能当端点）。平手时规则固定：航段更少优先，再按城市名序。
  错误类型显式：同城、城市不存在、起点/终点无服务、不可达。
- `pricing`：距离用 float 计算，**价格用 Decimal 按每段距离累加后 ×0.13，四舍五入到分（ROUND_HALF_UP）**；
  显示距离保留 1 位小数（公里）。规则写进 DESIGN 和设计文档，测试锁死。
- `places.resolve(name)`：Unicode 规范化 + 大小写/重音折叠精确匹配，失败则用 stdlib `difflib` 给候选
  （不引 rapidfuzz）。`nearest_served(city)`：711 个点暴力求最近有服务城市（数据小，暴力比 KD-tree 更简单、
  更易验证）；对不存在于库的城市（如用户说了库外城市）返回「不存在」+ 候选，不编造坐标。
- 验证：单元测试 + hypothesis 属性测试，对照**独立实现的参考解**（手写 Bellman-Ford / 枚举 hub 对的
  Floyd-Warshall），在真实库和随机生成图上比对路线成本；全库所有 (起点, 终点) 对跑一遍一致性。

### agent/（迭代 03）
- `llm`：httpx 直接调 `/chat/completions`（OpenAI 兼容），不用 openai SDK、不用任何 agent 框架。
  配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`；超时、重试、单轮最大工具步数上限。
- `loop`：消息 → LLM → 校验 tool call 参数（pydantic）→ 分发 → 结果回填，上限 N 步；非法参数作为错误
  结果回给 LLM 让它改正（计入工具调用成功率统计）。
- 工具（暂定，迭代 03 定稿）：
  | 工具 | 作用 | 为什么这样设计 |
  |---|---|---|
  | `find_city(name)` | 解析城市名，返回规范名/候选/无服务+最近有服务城市 | 把模糊匹配和「无服务建议」做成确定性逻辑，LLM 不猜 |
  | `quote_route(origin, destination)` | 算最便宜路线，生成 **Quote**（id、航段、中转、距离、价格），前端渲染为行程卡 | 报价是不可变记录，确认和下单都引用同一个 quote id |
  | `book_quote(quote_id)` | 下单 | 受确认闸门保护（见下） |
  | `list_my_tickets(filter?)` | 查当前用户已购票（按城市/日期/编号筛） | 用户票据只从 store 读，LLM 不记忆 |
- **确认闸门（硬规则，服务端执行，不靠 prompt）**：Quote 状态 `quoted → presented → booked / expired`。
  `book_quote` 仅当：quote 属于当前用户和当前会话、已作为卡片展示给用户、在其后有一条用户消息、
  未过期、未被更新的报价取代、未下过单。行程卡上也有「确认预订」按钮走同一个闸门。
  这些写成 state_invariants 的编号规则 + 模型测试。
- **LLM 测试**：
  - 常规测试：脚本化假 LLM 回放 → 覆盖 loop、分发、闸门，零成本、确定。
  - `backend/evals/`（按需跑真模型）：工具调用成功率（每个用例跑 N 次，按工具统计，设阈值）；
    轨迹测试（多轮对话，检查工具调用序列和不变式：未经确认不下单、回复中的数字都来自工具结果、
    无服务城市给出最近城市建议、查询已购票走 `list_my_tickets`）。结果记录模型、日期、通过率、费用。

### store/
- 独立 `app.db`（SQLite，stdlib `sqlite3`，不用 ORM）。locations.db 永远只读打开（`mode=ro`）。
- 表：users、conversations、messages（含角色、文本、卡片 JSON）、tool_calls（参数、结果、耗时、错误）、
  quotes、tickets（ticket 含全部航段快照）。会话历史和工具轨迹都可回看，也给 eval 复用。

### web/（Next.js，迭代 04）
- 页面：登录（只输用户名）、聊天（左侧会话列表、右侧对话 + 行程卡/票卡）、我的机票、历史会话只读查看。
- 依赖：next、react、typescript、vitest；样式用 CSS Modules，不引 UI 组件库/状态库。自有组件层在 DESIGN §5。
- 非流式请求起步（一轮一次 POST）；流式输出放「下一步」。
- 审查：Impeccable critique / audit / detect（见 AGENTS.md）。

## 依赖清单（每个都要理由）
- 后端：`fastapi` + `uvicorn`（服务）、`httpx`（LLM 调用和测试客户端）、`networkx`（Dijkstra，稳定、纯 Python、
  图很小够快）、`pydantic`（随 fastapi，tool 参数校验）；测试：`pytest`、`hypothesis`。用 `uv` 管理。
- 前端：`next`、`react`、`react-dom`、`typescript`、`vitest`。
- 先调研、后决定（迭代 02/03 开头各做一次 GitHub 搜索并记录结论）：「hub 约束最短路」现成实现、
  轻量 OpenAI 兼容 tool-calling loop 的参考实现、LLM 轨迹评测的现成格式。

## 迭代路线
| 迭代 | 内容 | 实施者 / 模型 | 验收档 |
|---|---|---|---|
| 02-core | 后端脚手架（uv、FastAPI 健康检查）、core/ 全部 + 属性测试 + 全对一致性 | worker，最强模型（算法与正确性） | A：测试差分 + lint |
| 03-agent | store/、llm client、loop、tools、确认闸门 + 不变式模型测试、假 LLM 测试、eval 框架和首批用例 | worker，最强模型 | A + 一次真模型 eval 报告 |
| 04-web | Next.js UI、API 对接、Impeccable 审查，补 DESIGN §3–6 | worker，前端用较快模型，审查由协调者 | A：真实界面截图 + Impeccable |
| 05-deliver | README（运行方式）、设计文档（题目要求的 5 个部分 + 可选项）、prompt history 导出、端到端演示 | 协调者 | A + 演示录屏 |

- 02 和 04 的前半（脚手架、静态页面）可以并行，最多 2 个 worker。API 契约由协调者在 03 开工前写定。
- **prompt history**：主会话和每个 worker 的原始会话都要导出到 `prompt history/`（含失败尝试），只删密钥。
  所以 worker 用 `tool/spawn_worker.sh` 启动后记录会话 id，交付前统一导出。

## Acceptance（本计划）
- 用户确认架构、工具设计、确认闸门、价格舍入规则、迭代路线和下面的开放问题。
- 确认后：写 02-core 详细计划（含测试清单），再开 worker。

## Open questions for the user
1. **下单方式**：推荐「自然语言确认 + 卡片按钮」两条路都通，都走服务端闸门；备选是只允许点按钮下单
   （最稳，但用户说"好的，订吧"时要再点一下按钮）。
2. **舍入**：价格按分四舍五入、距离显示 1 位小数，可以吗？
3. **报价有效期**：报价 15 分钟内有效，可以吗？（数据不变时价格也不变，有效期主要防止确认很久之前的报价）
4. **日期 / 乘客**：题目没要求航班日期、乘客姓名和座位。建议本期 ticket 只记下单时间和账户用户名，
   日期和乘客信息放进设计文档的「真实上线还需要什么」。是否同意？
5. **LLM key**：DeepSeek 的 key 你放进 `backend/.env`（已 gitignore）就行。eval 跑真模型会花少量费用，
   每次跑之前要问你吗，还是在一个上限内（例如每次 < $1）可以直接跑？
