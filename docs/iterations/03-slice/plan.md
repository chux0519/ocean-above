# 03-slice — 本地可运行的端到端版本（任务拆分）

User request (2026-11-23, verbatim): 「同时你可以拆分计划了，比如前后端相对比较独立的部分，依旧走 pfstack issue / pr /
验收的 loop，根据任务难易程度选取适当的模型做，需要控制预算。」

## Take-home check（2026-11-23）
| 要求 | 本迭代落点 | 结论 |
|---|---|---|
| 1 自然语言输入 | T3 agent loop + T6 聊天页 | 一致 |
| 2/3/4/5/6 数据、路线、价格 | 02-core（进行中）+ T2 `airline/` 复用，不重算 | 一致 |
| 7 下单前确认完整路线 / 距离 / 价格 | T2 出票条件 B1–B7 + T3 `request_booking` + T6 确认卡按钮 | 一致 |
| 8 无服务城市告知 + 最近有服务城市 | T2/T3 无服务记录 + T6 无服务卡 | 一致 |
| 9 已购票可自然语言查询 | T3 `list_my_tickets` + T5 `/api/tickets` + T6 我的机票 | 一致 |
| 10 可查看全部对话 | T5 会话接口 + T6 历史 | 一致 |
| 交付：本地可运行 + README | T7 compose + README「运行」 | 一致 |
无冲突。

## Goal
迭代结束时：`docker compose up` → 打开 `http://localhost:3000` → 固定账号登录 → 自然语言询价 → 确认卡点击出票 → 查票 → 看历史会话，
全程可演示；首版部署到 homelab（不公开）。界面只要求可用和移动端不破，打磨在 04。

## 流程（issue / PR / 验收 loop）
1. 协调者为每个任务开 GitHub issue：范围、拥有的文件、依赖、验收标准、模型、链接本计划。
2. worker：一个任务 = 一个 issue = 一个分支 `it03-<task>` = 一个 worktree；每个可验证单元 commit + push；
   完成后开 PR（`Closes #n`），PR 描述写验证结果；写 `docs/iterations/03-slice/<task>/STATE.md`。
3. 协调者验收：读 diff、`tool/review_lint.py`、`tool/test_diff.sh`、前端截图（ego-browser 桌面 + 手机）、对照 issue 验收标准；
   高风险 PR（T2、T3）另开一次只读评审。通过 → squash merge 到 `main` → 关 issue → 导出会话。
4. 每个任务最多 3 轮「实施 → 验收 → 修改」，超过就停下交给用户裁决。

## 任务拆分

| # | 任务 | 依赖 | 拥有的文件 | 模型（理由） |
|---|---|---|---|---|
| T1 | **API 与卡片契约**：HTTP 接口、SSE 事件、各类卡片 JSON 结构、错误码；`contract/fixtures/*.json` 示例，前后端测试共用 | 无 | `docs/api/contract.md`、`contract/**` | 协调者自己写（决定接口，不外包） |
| T2 | **store + airline**：app.db 表、迁移、`airline/`（报价、request_booking、nonce、出票 B1–B7、查票、trace）、出票不变式模型测试 | 02-core 合并 | `backend/app/store/**`、`backend/app/airline/**`、对应测试 | Fable 5.2（并发、状态、正确性，P0 风险） |
| T3 | **agent**：httpx OpenAI 兼容流式 client、agent loop、4 个工具、ReplyGuard、模板回退、SSE 事件生成、假 LLM 回放测试；先做约 20 例真模型的 ReplyGuard 实测 | T2 接口（可先对接口桩） | `backend/app/agent/**`、对应测试 | Fable 5.2（设计核心、P0 风险） |
| T4 | **LLM 评测**：`backend/evals/` 用例格式、运行器、工具调用成功率与轨迹检查、报告；首批用例（含 Abu Dhabi、San Jose、否定句不请求下单） | T3 | `backend/evals/**` | Sonnet 5.5（框架代码中等难度） |
| T5 | **HTTP API**：固定账号登录、会话 / 消息（SSE 包装 T3）、出票按钮接口、trace、tickets、`/api/health`、`/api/debug/sse-ping`、`/api/config`、归属检查、API 测试 | T2、T3 | `backend/app/api/**`、`backend/app/main.py`、对应测试 | Sonnet 5.5（按契约实现，中等） |
| T6 | **最简前端**：Next.js + TS + Tailwind + shadcn 基础组件；登录、聊天（SSE 读取、streamdown 渲染、卡片组件按契约 fixtures）、我的机票、历史会话；开发时 `/api` 转发；移动端不破；vitest 组件测试 | T1 | `frontend/**` | Sonnet 5.5（前端常规工作） |
| T7 | **compose + README**：`api` / `web` / `proxy`（Caddy）Dockerfile 与 compose、日志轮转、`.env.example`、README「本地运行」、homelab 首次部署 | T5、T6 | `docker-compose.yml`、`deploy/**`、`*/Dockerfile`、`.env.example`、README 运行一节 | Haiku 5.5（机械配置；部署由协调者执行） |

### 排期（同时最多 2 个 worker）
| 波次 | worker 1 | worker 2 | 协调者 |
|---|---|---|---|
| A（现在） | 02-core（进行中，Fable） | T6 前端（Sonnet，对 T1 fixtures 开发） | 先写 T1 契约，再开 T6 |
| B | T2 store + airline（Fable） | T6 继续 / T7 先做 Dockerfile 骨架（Haiku） | 审 02-core，合并 |
| C | T3 agent（Fable） | T5 API（Sonnet，T2 合并后） | 审 T2（含只读评审） |
| D | T4 评测（Sonnet） | T7 收尾（Haiku） | 审 T3（含只读评审）、T5；端到端联调；部署 homelab |

## 预算控制
- 模型按难度：Fable 5.2 只用于 02-core、T2、T3（正确性和 P0 风险集中处）；T4 / T5 / T6 用 Sonnet 5.5；T7 用 Haiku 5.5。
- 只读评审只给 T2、T3 各一次（Fable），其他 PR 由协调者审。
- worker 任务描述写清范围和拥有的文件，禁止扩需求；每任务最多 3 轮。
- 真模型评测：单次估算 < $1 直接跑，超出先问；平时测试一律用假 LLM。
- 同时最多 2 个 worker；任务卡住时先问协调者，不自己大范围探索。

## Acceptance（迭代级，tier A）
- 本地 `docker compose up` 后完成上面 Goal 里的完整流程，录一段 ego-browser 操作（桌面 + 手机截图各一套）。
- `tool/test_diff.sh` 无新失败；`tool/review_lint.py` 全过；出票不变式模型测试固定种子通过。
- 一次真模型评测报告（工具调用成功率、轨迹通过率、ReplyGuard 回退率、费用）。
- homelab 首次部署：`https://ocean-above.potafree.net/api/health` 200；`/api/debug/sse-ping` 首条事件及时到达。
- README「本地运行」可照做；设计文档同步更新；会话已导出。

## Open questions for the user
- 无（按上面的模型分配和排期执行，除非你要调整）。
