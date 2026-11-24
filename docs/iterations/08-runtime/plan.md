# 08-runtime — Agent SDK 评估与运行时迁移（#49），SQLite 限制文档与基准（#41）

User request (2026-11-24, verbatim): "Execute the Ocean Above roadmap in dependency order: 1. #49 — SDK POC, ADR, and
runtime migration 2. #48 — Backend Agent cancellation and Stop UI 3. #35 — OpenTelemetry + Phoenix tracing 4. #47 —
Multi-model benchmarks + optional Promptfoo. #41 may proceed independently. For each issue, read the full issue and
design docs, preserve B1–B7, run tests and evals, update documentation and prompt history, and do not start dependent
work before prerequisites are accepted."

本迭代只做 #49 和 #41。#48 → 09-stop，#35 → 10-otel，#47 → 11-bench，各自在前一项验收后再写计划开工。

## Take-home check（2026-11-24）
| 要求 | 本迭代影响 | 结论 |
|---|---|---|
| 1 自然语言；7 下单前确认完整路线 / 距离 / 价格 | 换运行时后，报价卡、确认卡、只能点击出票（B1–B7）、ReplyGuard 在输出前校验，全部不变；evals 是回归门槛 | 一致 |
| 价格 = 距离 × 0.13、只在 hub 中转、最便宜路线、最近服务城市 | `core/`、`airline/` 不动 | 一致 |
| 设计文档要写 agent 有哪些工具、为什么这样设计 | ADR 和 design.md §4 写清 SDK 负责什么、我们的代码负责什么 | 一致 |
| Prompt history 原始会话 | POC 失败、放弃的方案都留在会话里，照常导出 | 一致 |
| 没有对框架的限制 | — | 无冲突 |

**与 AGENTS.md 的冲突：** AGENTS.md 写了"不用 agent SDK，自己写 agent loop"。#49 由用户提出，明确说手写 loop 不是永久约束，
SDK 能减少维护代码就优先用。用户的新决定优先：若 ADR 选定 SDK，协调者同步改 AGENTS.md "Dependencies" 一节
（写明 SDK 只负责通用运行时，出票、校验、存储仍是我们的代码）。

## #49 阶段 1：POC + ADR（现在开始）
两个独立 POC 并行，各自是 `spikes/<name>/` 下的独立 uv 项目（自己的 pyproject + lock，不改 `backend/` 的依赖），
直接 import `backend/app` 的四个工具和 airline 服务，用真实 DeepSeek（`OCEAN_ENV_FILE` 读 key，脚本内读取，不打印）。

| # | 任务 | 拥有的文件 | 模型 |
|---|---|---|---|
| P-OA | OpenAI Agents SDK POC（`OpenAIChatCompletionsModel` + `AsyncOpenAI(base_url)`）：流式文本、串行和并行工具调用、工具错误、`result.cancel()`（immediate / after_turn）、工具执行中取消、上游 HTTP 流确实关闭、DeepSeek thinking 参数、增量 tool-call、usage（含取消时）、重试与报错、默认远程 trace 关闭；测量依赖树、镜像增量、冷启动、首 token、取消延迟；估算迁移后删掉 / 新增的应用代码行数；结果写 `docs/iterations/08-runtime/poc-openai-agents.md` | `spikes/openai-agents/**`、该报告 | Opus 5.5 |
| P-PA | Pydantic AI Slim `[openai]` 对照 POC：同一组检查和测量；结果写 `docs/iterations/08-runtime/poc-pydantic-ai.md` | `spikes/pydantic-ai/**`、该报告 | Opus 5.5 |
| ADR | 协调者读两份报告和代码，写 `docs/design/adr-001-agent-runtime.md`：采用 / 不采用，证据、取舍、迁移边界（SDK 管什么、我们管什么） | ADR | 协调者 |

真模型花费：每个 POC 预计 < $0.10（用户授权 < $1 每次不用问）。

**阶段 1 的通过条件（ADR 采用 SDK 的前提）：** 在 DeepSeek 上流式和多工具可用；取消能关闭上游流、不再调用新工具；
能在我们的代码里在输出前做 ReplyGuard（逐段缓冲）；能做每轮工具次数 / token / 步数 / 超时预算（SDK 自带或窄适配层）；
远程 trace 可关闭、可接自定义 processor；迁移后维护的运行时代码明显减少，依赖增量可接受。任一条不满足 → 选
`openai-python` 只替换模型客户端，或保留现有 loop，ADR 写明原因。

## #49 阶段 2–3：迁移（ADR 选定后）
一个 worker（Opus 5.5，分支 `it08-runtime`）按 ADR 改 `backend/app/agent/**`：四个工具改成 SDK function tool，
用户 / 会话 id 通过受信的 run context 注入；`Airline.book` 仍不在工具里；SDK 事件转成现有 SSE（status / card / delta /
replace / done / error）；ReplyGuard 仍在输出前；app.db 历史和压缩不变；预算、并发、锁、线程 drain 语义不变；删除旧 loop
和 httpx 客户端里被替代的部分。只建立 SDK 取消能力（给 #48 用）和 trace processor 接口（默认关闭远程导出，给 #35 用），
不做 Stop 接口 / UI，不做 Phoenix。
之后 Fable 只读评审一次（出票安全、预算、取消、并发），协调者跑真模型 evals 对比迁移前后。

## #41（并行，独立）
| # | 任务 | 拥有的文件 | 模型 |
|---|---|---|---|
| S | 文档：WAL / 单写者、按会话锁（进程内）、单 worker 限制、`_recheck` 在写事务内、SQLite 等锁与 conversation_busy 的区别、为什么这是有意的取舍，写成 design.md 新节草稿（协调者合并进 design.md）；基准工具 `backend/bench/`：`Store.write()` 等锁 / 持锁 / 总时长和 BUSY 计数（默认关闭的轻量计时）、`_recheck` 与图重建时长；隔离 app.db + locations.db 副本 + 假 LLM；场景（读写混合、出票突发、同会话双击、出票时 locations.db 变化）；并发 1/5/10/25/50/100；输出 JSON + Markdown；在本机跑一次，写实测结果和机器信息；迁移判断表（阈值标明是示例 SLO，不是测量结果）。不迁移数据库，不改出票逻辑 | `backend/bench/**`、`backend/app/store/db.py`（只加计时钩子）、`docs/iterations/08-runtime/sqlite/**` | Opus 5.5 |

## 排期（最多 4 个 worker）
| 波次 | worker | 协调者 |
|---|---|---|
| A | P-OA、P-PA、S 并行 | 审报告，写 ADR，审 S |
| B | 迁移 worker（S 可能还在跑） | 审迁移 PR，派 Fable 评审，跑真模型 evals |
| C | — | 验收，发布 VPS，导出会话，写 acceptance；然后写 09-stop 计划 |

## Acceptance（tier A）
- ADR 有实测证据（两个 POC 报告、依赖 / 镜像 / 延迟 / 行数）。
- 迁移后：`tool/test_diff.sh` 无新失败；lint；B1–B7 模型测试、参考路由测试、契约 fixture 测试、前端 vitest 全过；
  真模型 evals 和迁移前（06 的 100%，93 次）对比无安全回归，通过率、花费、延迟写进 acceptance。
- SSE 契约不变（或版本化并前后端一起改）；线上 e2e：报价 → 确认 → 点击出票、查票、历史、无服务城市、歧义城市。
- 默认没有任何外发 trace；Fable 评审无未处理的 P0/P1。
- #41：文档区分清楚 WAL、会话锁、单 worker；基准可复现，结果标明实测或未测；B1–B7 测试仍全过。
- design.md、README、AGENTS.md（依赖规则）、uv.lock 更新；会话导出。
