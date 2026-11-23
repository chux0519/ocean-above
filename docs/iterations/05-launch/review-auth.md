# 安全评审：05-launch L1（PR #27, fd0d5c9）+ L2（PR #28, c39f44c）

评审对象：main @ 8705771。只读；没有改仓库文件，没有读 `.env`，没有调用真实 LLM / Google。
验证方式：读代码 + 本地用假密钥、假时钟、假 fetch 跑 `GoogleVerifier` / `session` / `logins` 的探针（标「已复现」的条目）。

## 结论

- **没有发现 P0**：没有找到伪造 Google 身份、伪造会话 cookie、或跨用户读写数据的路径。
- **P1 三条**（公开前建议处理）：#1 邀请码按序号做账号键，撤销一个码会让别的码的用户登录进别人的账号；#2 JWKS 拉取失败时无限速重拉且在锁内阻塞，Google 不可达时可拖垮整个 API；#3 一个邀请码可以开无限个账号，绕过每用户日限额，一人可耗尽全站预算。
- **P2 九条**，见下。

| # | 级别 | 位置 | 一句话 |
|---|---|---|---|
| 1 | P1 | `backend/app/api/routes.py:164`、`logins.py:50` | 账号键用邀请码的序号，删码 / 调序后串号 |
| 2 | P1 | `backend/app/api/google.py:77,90,102-104` | JWKS 失败后每个请求都重拉（锁内、5 s 超时），并清掉仍有效的旧密钥 |
| 3 | P1 | `backend/app/api/routes.py:164`、`limits.py:27` | 同一邀请码换名字 = 新账号，每用户限额形同虚设 |
| 4 | P2 | `backend/app/api/logins.py:27,103` | `CF-Connecting-IP` 的信任只看对端是否私网；不经 Cloudflare 的部署可任意伪造，绕过登录限速 |
| 5 | P2 | `backend/app/api/logins.py:103-105`、`routes.py:86` | IPv6 按单个地址限速，一个 /64 即可绕过 |
| 6 | P2 | `backend/app/api/routes.py:164` | 邀请码 + 名字即账号，名字不是凭据：同码的人知道名字就能登录成对方（已写在文档里，需保证一人一码） |
| 7 | P2 | `backend/app/api/session.py:20-29`、`routes.py:56-63` | 会话无法撤销：注销只清 cookie；关掉 dev / 撤销邀请码后旧 cookie 仍有效 7 天 |
| 8 | P2 | `backend/app/api/session.py:25` | cookie 含非 ASCII 字符 → `TypeError` → 未登录也能触发 500（已复现） |
| 9 | P2 | `backend/app/api/google.py:143-147` | `exp` 为 NaN / Infinity 时通过过期检查（已复现；需要 Google 签名，属于加固） |
| 10 | P2 | `backend/app/store/db.py:367`、`records.py:215` | 全站预算只在开始时检查，且依赖上游返回 `usage`；tokens 为 NULL 的调用不计 |
| 11 | P2 | `backend/app/api/routes.py:39-49,208` | 请求体没有长度上限；登录体在限速和长度检查之前就被完整解析 |
| 12 | P2 | `backend/app/api/settings.py:47`、`routes.py:99-102` | `Secure` 默认关，线上靠记得设 `COOKIE_SECURE=1`；CSRF 只靠 SameSite=Lax，同站的其他 potafree.net 子域不受限（PLAUSIBLE） |

---

## P1

### 1. 邀请码的序号是账号键的一部分：撤销或调整码会串号
- 位置：`backend/app/api/routes.py:164`（`f"{index}:{name_key(name)}"`），`backend/app/api/logins.py:50-57`（`match` 返回列表下标），`.env.example:28`（"Append only"）。
- 问题：账号是 `(invite, "<码在 INVITE_CODES 里的下标>:<名字>")`。下标随列表内容变化。
- 场景：`INVITE_CODES=A,B,C`。码 A 泄露，运维把它删掉（撤销泄露的码正是最可能发生的操作）→ `B,C`。之后持 C 的评审「alice」登录，下标变成 1，拿到的是原来 B 下「alice」的账号：能看到对方的全部对话和机票，还能在对方的会话里继续操作。持 B 的人则落到原 A（已泄露码）名下的账号。重复的码、调换顺序同理。
- 现有防护只有 `.env.example` 的一句注释，代码不检查。
- 建议：账号键改用码本身的稳定标识，例如 `sha256(code)` 的前 16 个十六进制字符（`InviteCodes` 里已经有摘要），不用下标。撤销就是删掉该码，其余账号不受影响。上线前改，避免迁移已有账号。

### 2. JWKS 拉取失败：无限速重拉、锁内阻塞、清掉有效密钥（已复现）
- 位置：`backend/app/api/google.py:77`（失败也记 `_fetched_at`）、`:90`/`:94`（失败时 `self._keys = {}`）、`:102-104`（`fresh` 要求 `_keys` 非空，否则每次都 `_refresh()`）、`:100`（整个过程持锁）、`:55`（5 s 超时）。
- 问题有两个：
  1. 失败后 `_keys` 为空 → `fresh` 恒为假 → **每个**登录请求都同步重拉一次，`min_refetch` 不生效。模块文档说的「at most once per `min_refetch` seconds」在这条路径上不成立。探针：fetch 一直失败时，同一时刻 5 次 `verify` 触发 5 次 fetch。
  2. 密钥仍在有效期内时，任何人发一个带未知 `kid` 的伪造 token（每 60 s 允许一次重拉），如果这次重拉失败，原来有效的密钥被清空。探针：合法 token 先通过；伪造 `kid` + fetch 失败后，同一个合法 token 返回 `KeysUnavailable`（503）。
- 场景：api 容器经出站代理访问 `www.googleapis.com`（`.env.example:35-36`），代理抖动或不可达是现实情况。此时每个 `/api/auth/google` 请求持锁阻塞最多 5 s，其余请求排队等锁。这些是同步路由，跑在 Starlette 的线程池里（默认 40 个线程）；每个 IP 每分钟 10 次，几个 IP 就能占满线程池，于是 `/api/me`、`/api/conversations`、`/api/tickets`、邀请码登录等所有同步接口一起卡住。未登录即可触发。
- 建议：
  - 重拉失败时保留旧密钥继续用（设一个上限，例如最长 24 h），只在从未成功过时才返回 503。
  - `min_refetch` 对失败也生效：距上次尝试不足 60 s 时直接返回 503 / unknown kid，不发请求。
  - 在锁外拉取（锁只保护替换），或把超时降到 2–3 s。
  - 加两条回归：「失败后 60 s 内只拉一次」「重拉失败不影响已缓存 kid 的 token」。

### 3. 一个邀请码可以开无限个账号，绕过每用户日限额
- 位置：`backend/app/api/routes.py:151-165`，`backend/app/api/limits.py:27-34`，`backend/app/store/db.py:365`。
- 问题：限额按 `user_id` 计数，而邀请账号由「码 + 任意名字」决定。换一个名字就是一个新用户、新的 50 条额度。
- 场景：持有一个码的人（或拿到泄露码的人）循环 `POST /api/auth/invite {code, name: "a1"}`、`"a2"`…，登录限速允许每 IP 每分钟 10 个新账号，即每分钟 500 条消息额度。U1（每用户 ≤ 50）字面上仍成立，但起不到限制作用；实际上限只剩全站 200 万 token 预算，一个人可以在当天把它耗尽，其他评审全部收到 `scope: site` 的 429，直到 00:00 UTC。每天可重复。
- Google 账号也能多开，但成本高得多，可以接受。
- 建议（任选其一或组合）：每个码限制可创建的账号数（例如 5 个，超出返回 403）；或对邀请用户再按码计一个日限额；或给每个码单独的 token 预算。#1 改成按码摘要做键后，这里按同一个摘要计数即可。

---

## P2

### 4. `CF-Connecting-IP` 的信任条件太宽
- 位置：`backend/app/api/logins.py:27-28`（默认信任全部私网段）、`:101-105`。
- 现状（homelab）：Caddy 只监听 `127.0.0.1`（`docker-compose.yml:47`），唯一入口是 cloudflared，Cloudflare 边缘会覆盖 `CF-Connecting-IP`，所以**当前拓扑下不可伪造**。Caddy 默认不信任入站 `X-Forwarded-For` 并会重写它，这条也没问题。
- 问题：信任依据是「TCP 对端在私网段」，而不是「请求确实经过 Cloudflare」。`deploy/Caddyfile` 不清理这个头。
- 场景：(a) AGENTS.md 提到可能搬到 VPS 用同一份 compose；如果那里 Caddy 直接对外或前面换成别的反代，客户端自己带 `CF-Connecting-IP: <随机>`，每个请求一个新限速键，登录限速完全失效，邀请码和 dev 密码可以不限速地猜。(b) 开发时 api 绑 `0.0.0.0`，局域网里的客户端（对端 192.168.x，属于默认信任段）同样可以伪造。探针：`client_ip("192.168.1.50", {"x-forwarded-for": "8.8.8.8"})` → `8.8.8.8`。
- 建议：加一个显式开关（例如 `TRUST_CF_CONNECTING_IP=1`，默认关），只在确认前面是 Cloudflare 时打开；或在 Caddyfile 里对不是来自 cloudflared 的请求 `request_header -CF-Connecting-IP`。把这条写进 `docs/deploy.md` 的 VPS 部分。

### 5. IPv6 按单个地址限速
- 位置：`backend/app/api/logins.py:103-105`，`routes.py:86-87`。
- 场景：Cloudflare 会把 IPv6 客户端地址原样放进 `CF-Connecting-IP`。一个家用宽带或 VPS 通常有整个 /64，攻击者每次请求换一个源地址，每个地址都有独立的 10 次/分钟。对邀请码枚举（只要码有足够随机性就无碍）影响有限，但会放大 #2、#3、#11。
- 建议：IPv6 取 /64 前缀作为限速键；另加一个全局的失败登录上限（例如全站每分钟 100 次失败）兜底。

### 6. 邀请账号的名字不是凭据
- 位置：`backend/app/api/routes.py:158-164`，`logins.py:6-7`（已在文档里说明）。
- 场景：如果一个码发给多个人，其中任何人输入别人的名字（评审的名字通常猜得到；大小写、全角、`ß`/`ss` 都归一到同一个键，探针已确认）就登录成对方，看到对方的对话和机票，并能点对方会话里的确认卡出票。
- 建议：部署时保证一人一码，并在 README / 登录页写明「同一个码 + 同一个名字 = 同一个账号」。配合 #3 的每码账号数上限，一人一码时把上限设小即可。

### 7. 会话不能撤销
- 位置：`backend/app/api/session.py:15-29`，`routes.py:56-63`（只检查用户行是否存在），`routes.py:167-172`。
- 场景：(a) 注销只让浏览器删 cookie，被复制走的 cookie 在到期前（7 天）一直有效。(b) 线上删掉 `DEV_AUTH_*` 后，之前签发的 dev 用户 cookie 仍然可用（只要 `SESSION_SECRET` 没换、用户行还在）。(c) 撤销一个泄露的邀请码后，用它登录过的人会话仍然有效 7 天。
- 建议：最小改动是在 `current_user` 里检查该用户的登录方式当前仍启用（dev 开关、邀请码摘要仍在列表里，#1 改完后可直接查）；并在 `docs/deploy.md` 写明「撤销码 / 关 dev 时同时更换 `SESSION_SECRET`」。需要真正的注销可在 users 表加 `session_epoch`，签进 cookie。

### 8. 非 ASCII cookie 触发 500（已复现）
- 位置：`backend/app/api/session.py:25`。
- 问题：`hmac.compare_digest` 对含非 ASCII 字符的 `str` 抛 `TypeError`。探针：`session.verify(b"k", "u_1.123.é")` → `TypeError`。HTTP 头按 latin-1 解码，所以 `Cookie: oa_session=u_1.1.é` 会让任何需要登录的接口返回 500 而不是 401（HTTP 层未实际发请求验证，按代码路径判断）。
- 影响：没有绕过，只是未登录可触发的 500 和日志噪声。
- 建议：比较前都 `.encode()`（`routes.py:66` 的 `_octets` 已有同样的处理），或先判断 `value.isascii()`。

### 9. `exp` 为 NaN / Infinity 时不算过期（已复现）
- 位置：`backend/app/api/google.py:143-147`，`:160`。
- 问题：Python 的 JSON 解析接受 `NaN` / `Infinity`；`_number` 把它们当作数字，`nan <= now - 60` 为假，于是通过。探针：`exp: NaN` 和 `exp: Infinity` 的 token 都返回了身份。
- 影响：需要 Google 的有效签名，Google 不会签这样的 token，所以不可利用；属于加固。
- 建议：`_number` 加 `math.isfinite`；顺便限制 `exp - iat` 的最大跨度（例如 ≤ 1 天）。

### 10. 全站预算的两个漏算点
- 位置：`backend/app/store/db.py:361-369`，`backend/app/store/records.py:215-218`，`backend/app/agent/llm.py:51-52`。
- 问题：(a) 预算只在消息开始时检查，已经在跑的回合不受限（state_invariants U1 已记录）。结合 #3，同时发起的回合数没有上限，超出量 = 并发回合数 × 单回合成本（最多 6 步，每步约 1 万 token 上下文 + 4096 输出）。(b) 计数完全依赖上游在流末尾返回 `usage.total_tokens`；流中途出错、被取消、或换了不返回 usage 的服务时，`tokens` 为 NULL，按 0 计，预算永远不会触发。
- 建议：tokens 为 NULL 时按估算值记（例如请求字符数 / 3 + `max_tokens`）；上线后在 `docs/deploy.md` 的验证步骤里确认 `llm_calls.tokens` 确实有值。并发超出部分在修掉 #3 后可以接受。

### 11. 请求体没有长度上限
- 位置：`backend/app/api/routes.py:39-49`（`credential`、`code`、`name`、`text` 都没有 `max_length`），`:123/:138/:157`（限速在请求体解析之后），`google.py:114`（8192 的检查在解析之后）。
- 场景：未登录向 `/api/auth/invite` 发几十 MB 的 `code`，服务端完整读入、JSON 解析、再做一次 SHA-256，然后才计入限速。Cloudflare 免费档单请求上限约 100 MB。单 worker 进程，内存和 CPU 都会被占用。
- 建议：给这些字段加 `max_length`（code 200、name 200、credential 8192、nonce 200、text 取 `MAX_USER_CHARS` 的几倍），并在 Caddy 的 `/api/*` 上加 `request_body { max_size 64KB }`。

### 12. `Secure` 标志与 CSRF（后半部分 PLAUSIBLE）
- 位置：`backend/app/api/settings.py:47`，`routes.py:99-102`，`docs/deploy.md:51`。
- `Secure` 默认关闭，线上依赖 `.env` 里设 `COOKIE_SECURE=1`（deploy.md 已写）。漏设时 cookie 会随明文 HTTP 请求发出。建议：配置了 `GOOGLE_CLIENT_ID` 而 `COOKIE_SECURE` 未开时，启动打一条警告；并在 D 任务的线上验证里检查 `Set-Cookie` 带 `Secure`。
- CSRF 只靠 `SameSite=Lax`。SameSite 按「站」（potafree.net）判断，homelab 上其他 `*.potafree.net` 子域的页面发来的 POST 会带上 cookie。FastAPI 在没有 `Content-Type` 头时仍按 JSON 解析请求体，`POST /api/conversations` 接受任意内容类型，所以这类请求不会触发 CORS 预检。影响有限：出票需要 nonce，跨源读不到；能做的是替受害者新建会话、发消息消耗额度。前提是某个兄弟子域存在 XSS 或不可信内容，所以标 PLAUSIBLE。建议：对所有非 GET 的 `/api/*` 校验 `Origin`（或 `Sec-Fetch-Site: same-origin`），几行中间件即可。

---

## 检查过、没有发现问题的项

- **算法混淆**：只接受 `RS256`（头部检查 + `jwt.decode(algorithms=...)`），JWKS 里只取 `kty=RSA` 的条目；HS256 / `none` 无法通过。
- **kid**：只用来在 Google 的密钥集合里选钥，未知 kid → 401；伪造 kid 触发的重拉在密钥正常时每 60 s 最多一次。
- **aud / iss**：`aud` 必须包含本应用 client id，`iss` 限定两个 Google 值；`sub`、`exp`、`iat` 必需。`aud` 为数组且包含 client id 时通过（探针确认），Google 不签发这种 token，可接受。
- **email_verified**：必须是布尔 `true`，字符串 `"true"` 被拒（探针确认）。
- **JWKS 来源**：固定 HTTPS URL，不跟随重定向；经 `HTTPS_PROXY` 时是 CONNECT 隧道，TLS 仍是端到端。没有找到缓存投毒路径。
- **账号键冲突**：`UNIQUE(auth_provider, provider_sub)`，三种登录方式互不重叠；邀请键的下标是整数，`:` 分隔无歧义。
- **会话 cookie**：HMAC-SHA256（截 160 位）覆盖 `user_id.expires`，常数时间比较，过期检查在签名之后；`user_id` 是服务端生成的 `u_<hex>`，不含 `.`。HttpOnly、SameSite=Lax、Path=/。
- **邀请码**：只存摘要，逐个常数时间比较；先验码再验名字，不泄露码是否接近。
- **dev 登录**：两个变量都设置才开启，关闭时 404；比较为常数时间。
- **限额并发与跨天**：计数和占位在同一个 `BEGIN IMMEDIATE` 事务里；按 UTC 日的字符串比较正确；文本无效时占位会退回（`stream_turn` 的校验是同步的，在返回流之前抛出）；退回按 `user_id` 限定。
- **跨用户访问**：会话、消息、报价、出票、trace、机票都带 `user_id` 检查，别人的对象返回 404。
- **日志与错误**：Google 拒绝原因只记原因字符串，不记 token；JWKS 失败只记异常类型；`Settings` 的密钥字段 `repr=False`；错误响应不含内部细节。
- **uvicorn 代理头**：默认只信任来自 127.0.0.1 的转发头，Caddy 在容器网络里不是这个地址，所以 `request.client.host` 是 Caddy 的地址，由 `client_ip` 自己处理。

## 测试缺口

- 没有覆盖：JWKS 失败后的重拉频率、重拉失败时保留旧密钥（#2）；`INVITE_CODES` 变更后的账号归属（#1）；同码多名字的额度（#3）；非 ASCII cookie（#8）；`exp` 为 NaN / Infinity（#9）。
