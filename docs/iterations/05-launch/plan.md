# 05-launch — 登录、多用户、用量限额，公开 demo

User request (2026-11-23, verbatim): 「1. 可以，我们缓存字体 2. 可以我们开始做登录 多用户 以及用量限额，告诉我怎么配置 google project」

## Take-home check（2026-11-23）
| 要求 | 本迭代影响 | 结论 |
|---|---|---|
| 非必须 2：用户系统可以很简单；用第三方要清楚它怎么工作 | Google 登录只取 ID token，后端自己验签、发自己的会话；设计文档写清楚流程和信任边界 | 一致 |
| 9 用户的全部机票 / 10 全部对话 | 多用户隔离：每个接口按 user_id 过滤，跨用户 404（已有），加双用户端到端测试 | 一致 |
| 交付：评审能运行 | 本地 `docker compose up` 不依赖 Google（固定账号 / 邀请码登录仍可用）；字体改为仓库内文件，离线也能构建 | 一致 |
无冲突。

## Goal
`https://ocean-above.potafree.net` 可以公开给评审：Google 登录或邀请码登录，多用户数据隔离，每用户每日消息上限和全站每日
token 预算，超额有明确提示；dev 固定账号在线上关闭。本地运行仍然零配置可用。

## 设计要点（写进 design.md §8.1–8.2）
- **Google 登录**：前端用 Google Identity Services（GIS）按钮拿 ID token → `POST /api/auth/google {credential}` → 后端用
  Google JWKS 验签（缓存 JWKS，按 `kid` 选钥），检查 `aud == GOOGLE_CLIENT_ID`、`iss ∈ {accounts.google.com,
  https://accounts.google.com}`、`exp`、`email_verified`；通过后 upsert 用户（`auth_provider=google`, `provider_sub=sub`），
  发我们自己的 `oa_session`。之后不再依赖 Google。不需要 client secret，不申请任何 Google API 权限。
  依赖：`PyJWT[crypto]`（验签 + JWKS 客户端，比 `google-auth` 小，不带 requests）。
- **邀请码登录**（给评审，不依赖 Google）：`INVITE_CODES`（逗号分隔，存 hash 比较）+ 显示名 → 用户
  `auth_provider=invite`, `provider_sub=<code id>:<name>`；同样受限额约束。
- **dev 固定账号**：只在 `DEV_AUTH_USER/PASSWORD` 都设置时开启（本地默认开，线上 `.env` 删掉即关）。
- **`/api/config`**：`{dev_login, invite_login, google_client_id}`，前端据此显示登录方式（运行时配置，同一镜像）。
- **限额**：`DAILY_MESSAGES_PER_USER`（默认 50）、`DAILY_TOKEN_BUDGET`（全站，默认 2,000,000 tokens，按
  `llm_calls.tokens` 累计，UTC 日）；超额时 `POST .../messages` 在发响应头前返回 `429 rate_limited`（message 说明是
  个人上限还是全站预算、何时重置），不存消息；出票、查票、看历史不受限。`GET /api/me` 带 `usage: {messages_today,
  daily_limit}`。登录接口按 IP 简单限速（每分钟 10 次）。
- **会话标题兜底**：标题为空时用首条用户消息，再空则 "New conversation"（04 遗留）。
- **字体**：Public Sans woff2 放进 `frontend/src/fonts/`，`next/font/local` 加载（04 决定：缓存字体）。
- **COOP 头**：GIS 弹窗需要 `Cross-Origin-Opener-Policy: same-origin-allow-popups`（Next 配置或 Caddy）。

## 任务
| # | 任务 | 依赖 | 拥有的文件 | 模型 |
|---|---|---|---|---|
| C2 | 契约 v1.2：`/api/auth/google`、`/api/auth/invite`、config 字段、`me.usage`、`429 rate_limited`、标题兜底 | 无 | `docs/api/contract.md`、`contract/**` | 协调者 |
| L1 | 后端认证：Google ID token 验签（PyJWT + JWKS 缓存，可注入时钟和密钥做测试）、邀请码、dev 开关、登录限速、标题兜底；测试（伪造签名、错 aud/iss、过期、未验证邮箱、kid 轮换、JWKS 拉取失败） | C2 | `backend/app/api/**`（auth 部分）、`backend/app/store/**`（用户）、测试 | Opus 5.5（安全） |
| L2 | 用量限额：计数与预算、429、`me.usage`、并发下不超额（同一事务计数）；测试 | C2 | `backend/app/api/**`（限额部分）、`backend/app/store/**`（计数查询）、测试 | Sonnet 5.5 |
| L3 | 前端：GIS 按钮（`/api/config` 驱动）、邀请码表单、账号显示、429 提示（输入保留）、本地字体、COOP 头；vitest | C2 | `frontend/**`、`deploy/Caddyfile`（仅 COOP 头） | Sonnet 5.5 |
| R | Fable 只读安全评审 L1 + L2 | L1、L2 | — | Fable 5.2 |
| D | 部署：homelab `.env` 加 `GOOGLE_CLIENT_ID`、`INVITE_CODES`、限额变量，删 dev 账号；双用户端到端（Google + 邀请码）；公开 | 全部 | `docs/deploy.md` | 协调者 |

L1 与 L2 都改 `backend/app/api/`：L1 先做，L2 在 L1 合并后开始（或 L2 只动独立的 `limits.py` + 路由一行接入，冲突小，
可并行；协调者开工时按实际代码结构决定）。L3 可与 L1 并行（对契约开发）。

## 预算控制
- Opus 只用于 L1；L2、L3 用 Sonnet；Fable 一次只读评审。真模型只在 D 的端到端里用（几分钱）。

## Acceptance（tier A + 线上验证）
- 测试差分无新失败；lint；认证与限额的单元 / API 测试；前端 vitest。
- Fable 评审无未处理的 P0/P1。
- 线上：Google 账号 A、邀请码用户 B 各自询价、出票、查票、看历史，互相看不到对方数据；超额返回提示；dev 账号已关闭。
- 本地：无 Google 配置时 `docker compose up` 可用（dev / 邀请码登录），离线可构建前端。
- 设计文档 §8.1–8.2 更新（含第三方登录怎么工作）；README 写登录方式和限额变量；会话导出。

## 需要用户做的
- 按下方步骤配置 Google Cloud 项目，把 **Client ID** 发给我（不是机密，不需要 client secret）。
- 决定邀请码：给评审几个码（我生成随机码，通过安全渠道放进 homelab `.env`，码本身发给你转交评审）。
