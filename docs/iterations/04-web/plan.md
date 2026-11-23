# 04-web — 界面完善、移动端、设计规范

User request (2026-11-23): 「可以 开始」（对「先写 04 计划给你确认」的回复）。

## Take-home check（2026-11-23）
| 要求 | 本迭代影响 | 结论 |
|---|---|---|
| 1 自然语言 | 聊天体验（加载、错误、重试） | 一致 |
| 7 下单前确认完整路线 / 距离 / 价格 | 行程卡、确认卡的视觉层级必须让航段、中转、距离、价格一眼可见；已出票 / 过期状态清楚 | 一致，验收重点 |
| 8 无服务城市 + 最近城市 | 无服务卡、歧义卡（San Jose / San José 的区分信息） | 一致 |
| 9 查已购票 | 我的机票页：加载、空状态 | 一致 |
| 10 查看全部对话 | 历史页：加载、空状态 | 一致 |
| 数字正确性 | 前端仍只显示 `*_display`，不格式化数字 | 不变 |
无冲突。

## Goal
在不改变业务行为的前提下，把 03 的可用界面打磨成可以给评审看的 demo：清晰的视觉规范（DESIGN.md §2–6）、完整的状态
（加载 / 空 / 错误 / 重试）、移动端无瑕疵，通过 Impeccable 审查（P0–P1 清零），部署到 homelab。

## 任务

| # | 任务 | 依赖 | 拥有的文件 | 模型 |
|---|---|---|---|---|
| C1 | 契约：QuoteCard / BookingRequestCard 增加 `similar_names: {city, connected_hubs}[]`（端点有去重音同名城市时非空） | 无 | `docs/api/contract.md`、`contract/fixtures/**` | 协调者 |
| B1 | 后端按 C1 在卡片里输出 `similar_names`；测试 | C1 | `backend/app/airline/**`、测试 | Sonnet 5.5（小改动） |
| F1 | 功能性 UI：我的机票 / 历史 / 聊天的加载、空、错误状态；出票错误码的提示（`stale` 引导重新报价、`conversation_busy` 保留输入并可重试、`data_unavailable` 可重试、`expired`）；已出票卡片隐藏有效期、未出票卡片显示倒计时；手机上票号标签不折行；`similar_names` 区分信息；streamdown 瘦身（只保留需要的插件，或换 `react-markdown`，前后体积写进 STATE） | C1 | `frontend/**` | Sonnet 5.5 |
| D1 | 视觉设计：先装 Impeccable（协调者执行 `npx impeccable install`，项目级，启用设计 hook），worker 跑 `/impeccable init`（PRODUCT.md）→ 提出 DESIGN.md §2–6 方案（色板、字号、间距、圆角、动效、组件清单、各页面层级）写成提案文件 → 用户确认 → 实现（Tailwind 主题 token + shadcn 组件统一）→ `/impeccable critique`、`/impeccable audit`、`npx impeccable detect`（源码 + 运行中的页面）→ P0–P1 清零 | F1 合并后（避免同时改前端） | `frontend/**`、`docs/iterations/04-web/D1/**`、`PRODUCT.md` | Opus 5.5（设计质量） |

DESIGN.md 只由协调者改：D1 写提案 `docs/iterations/04-web/D1/design-proposal.md`（含桌面和手机的效果截图），
用户确认后由协调者写进 DESIGN.md；与 DESIGN.md 冲突的审查建议列成「设计冲突」表交用户裁决。

### 排期（同时最多 2 个 worker）
| 波次 | worker 1 | worker 2 | 协调者 |
|---|---|---|---|
| A | F1（Sonnet） | B1（Sonnet） | 先写 C1；审 B1、F1 |
| B | D1（Opus）：提案 → 等用户确认 → 实现 + 审查 | — | 安装 Impeccable；把确认的方案写进 DESIGN.md；审 D1 |
| C | — | — | 部署 homelab；ego-browser 桌面 + 手机全流程截图；验收 |

## 预算控制
- Opus 只用于 D1；F1、B1 用 Sonnet。不做 Fable 评审（本迭代无出票 / 数字逻辑改动；若 B1 触及出票路径则补一次）。
- 不调用真模型；前端测试用 mock API，端到端截图只在 C 波次跑一次真流程（几分钱）。

## Acceptance（tier A）
- `tool/test_diff.sh` 无新失败；lint；前端 vitest 覆盖新增状态和错误提示。
- Impeccable：critique 与 audit 报告存档，P0–P1 清零；`detect` 对源码和运行页面无高优先级发现（或逐条说明）。
- ego-browser 截图：桌面 1440×900 与手机 390×844，覆盖登录、聊天（报价、确认、出票、无服务、歧义、错误）、我的机票、
  历史；无横向滚动。
- DESIGN.md §2–6 填写完成（用户确认过的方案），`DESIGN.md` 不再有占位符（`pfstack check` 通过）。
- homelab 已部署新版本；README 截图 / 说明按需更新；会话导出。

## Open questions for the user
- 无。D1 的视觉方案会先给你看提案和截图，你确认后才实现。
