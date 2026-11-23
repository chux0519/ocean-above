# 01-architecture — 模块拆分、整体设计和迭代路线

User request (2026-11-23, verbatim): 「按 pfstack 开始：先写计划给我确认」（前文：「完成之后我们开始做模块拆分和设计」）

> 注意：本计划正文是 v0 时的设计；架构细节以 `docs/design/design.md`（v2 起）为准，下方「用户决定」「追加决定」
> 「设计评审后的修改」记录了变化。

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
- `pricing`：**计算和存储都用精确值，只在显示时舍入**。坐标是整数，每段距离 = `Decimal(dx²+dy²).sqrt()`
  （高精度 Decimal 上下文，如 50 位），总距离 = 各段精确值之和，价格 = 总距离 × `Decimal("0.13")`；
  app.db 里以十进制字符串存精确值，不存舍入值，舍入值也不参与任何后续计算。显示：距离 1 位小数，价格到分
  （ROUND_HALF_UP）。路线比较也用精确值。测试锁死这些规则。
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
  未过期（15 分钟）、未被更新的报价取代、未下过单。行程卡上也有「确认预订」按钮走同一个闸门。
- **下单前复核数据（新鲜度）**：Network 按 locations.db 的指纹（mtime + size + sha256）缓存，指纹变就重载。
  quote 记录生成时的指纹。`book_quote` 时：重读指纹 → 用当前 locations.db 重算这条路线（航段、精确距离、
  价格）→ 与 app.db 中的 quote 逐项比对，并确认票据表里没有同一 quote 的票。任一不一致 → 不下单，
  quote 置为 `stale`，给出新报价并要求用户重新确认。
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
- 依赖：next、react、typescript、vitest、Tailwind CSS、shadcn/ui；不引状态管理库。组件层在 DESIGN §5。
- 移动端：手机宽度（≥ 360px）下会话列表折叠为抽屉，行程卡单列；ego-browser 在桌面和手机两个视口截图验收。
- 非流式请求起步（一轮一次 POST）；流式输出放「下一步」。
- 审查：Impeccable critique / audit / detect（见 AGENTS.md）。

## 依赖清单（每个都要理由）
- 后端：`fastapi` + `uvicorn`（服务）、`httpx`（LLM 调用和测试客户端）、`networkx`（Dijkstra，稳定、纯 Python、
  图很小够快）、`pydantic`（随 fastapi，tool 参数校验）；测试：`pytest`、`hypothesis`。用 `uv` 管理。
- 前端：`next`、`react`、`react-dom`、`typescript`、`vitest`、`tailwindcss`、shadcn/ui（按需拷入的组件，
  会带上 Radix 原语）。
- 先调研、后决定（迭代 02/03 开头各做一次 GitHub 搜索并记录结论）：「hub 约束最短路」现成实现、
  轻量 OpenAI 兼容 tool-calling loop 的参考实现、LLM 轨迹评测的现成格式。

## 迭代路线
| 迭代 | 内容 | 实施者 / 模型 | 验收档 |
|---|---|---|---|
| 02-core | 后端脚手架（uv、FastAPI 健康检查）、core/ 全部 + 属性测试 + 全对一致性 | worker，最强模型（算法与正确性） | A：测试差分 + lint |
| 03-slice | store/、`airline/`、llm client、loop、tools、ReplyGuard、出票条件 B1–B7 + 模型测试、SSE、假 LLM 测试、eval 框架和首批用例、API（固定账号）、**最简前端**（聊天 / 我的机票 / 历史）、docker compose、README 初稿。**结束时本地 `docker compose up` 可端到端演示** | worker，最强模型（前端可另开较快模型 worker） | A + 一次真模型 eval 报告 |
| 04-web | shadcn/Tailwind UI、移动端、streamdown、全部卡片、计算依据、补 DESIGN §3–6；部署到 homelab（不公开） | worker，前端用较快模型，审查由协调者 | A：ego-browser 桌面 + 手机截图、Impeccable + shadcn 审查 |
| 05-launch | Google 登录 + 多用户、邀请码登录、限额与预算、OAuth 发布、demo 公开 | worker + 协调者 | A + 线上验证 |
| 06-deliver | 英文最终设计文档（按 take-home 5 个标题）、README 定稿、最终 eval 报告、prompt history 最终导出与脱敏扫描 | 协调者 | A |

- 02 和 04 的前半（脚手架、静态页面）可以并行，最多 2 个 worker。API 契约由协调者在 03 开工前写定。
- **prompt history**：主会话、每个 worker、评审会话、homelab 上协助部署的 agent 会话都要导出到 `prompt history/`
  （含失败尝试），只删密钥。用 `tool/export_sessions.py`，**每个迭代结束导出一次并提交**，不留到最后
  （Claude Code 默认只保留约 30 天的本地会话）。
- README 和设计文档每个迭代增量更新，不压到最后。

## 用户决定（2026-11-23）
1. 下单：自然语言确认和卡片按钮两条路都通，都走服务端闸门。
2. 精确值计算和存储，只在显示时舍入（已写进上文 pricing 和 AGENTS.md / review_rules M08）。
3. 报价 15 分钟有效；真实下单前复核 locations.db 和业务库（上文「下单前复核数据」，M09）。
4. 本期不做航班日期 / 乘客信息 / 座位，写进设计文档的「真实上线还需要什么」。
5. 真模型 eval 每次估算费用 < $1 可直接跑，超出先问。
6. 每个迭代开工前对照 take-home 检查计划，冲突以 take-home 为准（M07）。
7. 前端可以用组件库：**shadcn/ui + Tailwind CSS**，并用对应 skill / 规范审查，外加 Impeccable。
8. 前端移动端适配是一等需求；界面测试和截图用 ego-browser（ego lite），桌面 + 手机两个视口（M10）。

## Take-home check（2026-11-23）
| 要求 | 计划中的对应 | 结论 |
|---|---|---|
| 1 自然语言输入 | agent loop + 工具 | 一致 |
| 2/3 locations.db 唯一事实来源 | 只读打开、指纹重载、下单前复核，不复制数据 | 一致 |
| 4 只在 hub 中转、双向、空 connected_hubs 无服务 | routing 中非 hub 只做端点；无服务显式报错 | 一致 |
| 5 无直飞时找最便宜，转机算一张票 | Dijkstra（价格∝距离）；ticket 含全部航段 | 一致 |
| 6 距离 = 坐标距离和，价格 = 距离 × 0.13 | 精确 Decimal 计算，显示时舍入 | 一致 |
| 7 下单前确认完整路线、距离、价格 | 行程卡 + 服务端确认闸门 | 一致 |
| 8 无服务城市要告知并推荐最近有服务城市 | `find_city` / `quote_route` 返回最近有服务城市 | 一致 |
| 9 知道用户所有票，可自然语言查询 | `list_my_tickets` + 我的机票页 | 一致 |
| 10 可查看所有对话 | 会话列表 + 只读历史查看 | 一致 |
| 交付：README、设计文档 5 部分、prompt history 原始会话 | 迭代 05；worker 会话 id 全程记录 | 一致 |
| 非必须：localhost:3000 可接受 | 开发绑定 0.0.0.0、端口任意，README 写明；部署 Cloudflare 前再问 | 不冲突 |

## Acceptance（本计划）
- 用户确认架构、工具设计、确认闸门、价格舍入规则、迭代路线和下面的开放问题。
- 确认后：写 02-core 详细计划（含测试清单），再开 worker。

## 追加决定（2026-11-23，设计文档 v0 review 后）
- 流式输出提前到 03；前端用流式 Markdown 渲染组件（`streamdown` 优先）。
- Google 登录现在做（04），用户在 GCP 建 OAuth client；保留演示登录兜底（待确认）。
- 不上 MCP；`airline/` 作为领域边界，将来可拆成 MCP server。时刻 / 座位不实现、不编造。
- 详见 `docs/design/design.md` v1。

## 设计评审后的修改（2026-11-23，用户确认全部推荐）
- 见 `docs/design/design.md` v2 §12。迭代路线已按「先交付必交物」调整为上表。
- 数据事实补充：`San Jose`（直连 Tokyo）和 `San José`（直连 Sydney、Tokyo）是两个城市，去重音后同名；
  库内坐标是虚构的，最近有服务城市常与地理常识不同（Abu Dhabi → Tirana、Stuttgart → Novi Sad、Austin → Copenhagen）。

## Open questions for the user
- 无（已全部由用户决定，见上）。
