# Implementation — 02-core round 01

- Source state: `it02-core` @ `3a6dcc3`（被测提交；其后只有文档提交）
- 环境：macOS，uv 0.10.10，Python 3.12.8，networkx 3.7，fastapi 0.165.0，pytest 9.2.0，hypothesis 6.170.17
- Worker 会话 id：`6c19d2b6-f62b-4b7d-8048-ab879ac8ccc4`（Claude Code，最强模型）

## 0. GitHub 调研：「只在 hub 中转」有没有比 networkx 更合适的成熟方案

2026-11-23，`gh search repos` / `gh search code` + 一次网页搜索。结论：**没有，沿用 networkx**。

| 搜索词 | 结果 | 判断 |
|---|---|---|
| `hub-constrained shortest path`、`transfer only at hubs routing`、`hub and spoke shortest path`、`flight routing hub dijkstra`；代码搜索 `"transfer only at hubs"` | 0 个仓库 / 0 处代码（另有一个 0 星的课程作业 `vjain0812/flight_route_shortest_path_finder`） | 没有针对这条规则的专用库 |
| `hub labeling` | `savrus/hl`（C++，★60）、`lviennot/hub-labeling`（C++，★9）、`albvera/HHL_CSP`（★8） | 名字相近、含义不同：这是 2-hop labeling，给百万节点路网做查询加速的索引，和「只能在 hub 换乘」的约束无关；C++，无 Python 包 |
| `contraction hierarchies` | `UDST/pandana`（★434）、`GVeitBatz/KaTCH`、`LdDl/ch` | 同样是大图加速技术；我们的 hub 图只有 40 点 151 边 |
| `RAPTOR transit routing python`、`lviennot/hl-csa-raptor` | 基于时刻表的公交换乘算法 | 需要时刻表，locations.db 里没有 |
| `rustworkx`（`Qiskit/rustworkx`，★1786） | 通用图库，Rust 实现 | 能做同样的事，但也没有「限制中间点」的现成选项；多一个二进制依赖，40 个点上没有速度需求。不采用 |
| networkx 讨论区 #4832（constrained shortest path） | 维护者建议把约束建进图的结构（状态图） | 正是本实现的做法：非 hub 城市根本不进搜索图，只作端点 |

做法：约束通过**建图**表达（搜索图只含 hub 和 hub_links），不需要专用算法；networkx 3.7（★17k，纯 Python）
足够，且计划已列为依赖。无新增依赖。

## 1. 做了什么（按计划条目）

- **backend/ uv 项目**：`pyproject.toml` + `uv.lock`；运行依赖 fastapi、uvicorn、networkx；dev 依赖 pytest、
  hypothesis、httpx。包目录 `backend/app/`（`app/core/*`、`app/main.py`）。
- **network**（`app/core/network.py`）：`load_network(path)` 以 `file:…?mode=ro` 打开；`build_network(rows, links, fp)`
  做全部校验并构造不可变 `Network`（真实库和测试生成的图走同一段校验代码）。校验：城市名（非空、无首尾空白、
  不含 `;`、不重复）、坐标（`x,y`，整数或小数，不允许空格）、`is_hub` 只能是整数 0/1、`connected_hubs`（每项存在、
  是 hub、不重复、无空项；hub 必须为空）、`hub_links`（两端存在、是 hub、非自环）。任何一条不满足抛
  `NetworkDataError`，不修正。`Fingerprint(sha256, size, mtime_ns)`，`.id = "sha256:…"`。读取时前后各取一次
  stat + sha256，不一致则重读（最多 3 次），保证哈希和数据来自同一文件状态。
  `NetworkProvider.current()`：每次 stat，mtime/size 变了再算 sha256，内容真的变了才重载；`current(strict=True)`
  无条件重新哈希；重载失败时抛错且不再返回旧 Network。
- **pricing**（`app/core/pricing.py`）：`price_path(net, cities) -> Priced`，每次调用新建
  `Context(prec=50, rounding=ROUND_HALF_EVEN)`，全部运算用 `ctx.xxx(...)` 形式；按航段顺序求未舍入距离之和，
  × `Decimal("0.13")`；每段距离、总距离、价格各量化一次为 30 位小数字符串。它同时拒绝不可订的序列（未知城市、
  不存在的航段、在非 hub 中转、重复城市）→ `InvalidPathError`。`fmt_km`（1 位，HALF_UP，`"5.0 km"`）、
  `fmt_usd`（分，HALF_UP，`"$0.65"`）只用于显示。
- **routing**（`app/core/routing.py`）：`cheapest_route(net, origin, dest) -> Route | RouteError`。
  - 加载时 `build_hub_paths`：networkx 在 hub 图上对每个 hub 对求候选路径（真实库 1560 个有序对，各 1 条，
    加载总耗时约 0.12 s）。查询时枚举「起点直连 hub × 终点直连 hub」，拼出候选序列，全部交给 `price_path`，
    按（价格，航段数，城市名序列）取最小。networkx 的长度不出现在任何结果里。
  - 错误：`unknown_city`、`ambiguous_city`、`same_city`、`no_service`、`unreachable`。`RouteError.issues` 按端点
    给出明细（`role`、`query`、`city`、`candidates`、`nearest_served`）；两端都有问题时两条都返回。
  - `Route`：`origin`、`destination`、`cities`、`legs`（每段 origin / destination / distance）、`transfers`、
    `distance`、`price`、`fingerprint`（网络指纹 id）、`similar_names`（端点有折叠同名兄弟时）。
- **places**（`app/core/places.py`）：`resolve(net, name)`：NFC + 去首尾空白精确匹配 → 折叠键（NFKD 去重音、
  casefold、压缩空白，外加 NFKD 不分解的 `ø ł đ ð þ æ œ ı`，库里有 Tromsø、Wrocław）唯一命中 → 多个命中返回
  `ambiguous` + 全部候选 → 否则 difflib（cutoff 0.6）≤ 5 个候选。折叠索引是「键 → 城市元组」。
  `nearest_served(net, city, k=3, exclude=())`：暴力扫描全部有服务城市，按（距离，名字）排序。
- **API**（`app/main.py`）：`GET /health`（状态 + 城市 / hub / 航线 / 无服务计数 + 指纹）、`GET /api/cities`。
  库路径：`LOCATIONS_DB` 环境变量，默认仓库根目录的 `locations.db`；启动时即加载，库坏则启动失败。
- **README.md**：新建，只有「Backend development and tests」一节。

## 2. 测试（105 个，全过；整套约 39 s）

| 计划条目 | 文件 | 内容 |
|---|---|---|
| 1 加载 | `tests/test_network.py` | 真实库 711 / 40 / 151 / 10；加载不改动文件、不留 `-journal/-wal`；19 个坏样例**库文件**（坏坐标 5 种、坏 is_hub、坏引用、空项、重复、hub 带 connected_hubs、自环、非 hub 连线、重名…）各自报错；缺文件 / 缺表 / 非数据库文件 |
| 2 距离价格 | `tests/test_pricing.py` | 3-4-5 → `5.000…` km → `$0.65`；多段求和；√2 的 30 位存储值 vs 显示值；`fmt_usd` 在 .005 边界（含 1.125 → 1.13，区别于银行家舍入）；格式化不受调用方上下文影响；`price_path` 拒绝 8 种不可订序列 |
| 3 路线规则 | `tests/test_routing.py` | 直飞 hub–hub、直飞优于绕行、非 hub–其 hub、共享 hub、多次中转、在终点的多个 hub 中选便宜的、非 hub 不能当中转点（含因此不可达）、平手（航段数 → 名序，含无理数平手）、同城、未知（单端 / 两端）、歧义、无服务（起点 / 终点 / 两端 / 排除另一端）、构造的不连通图 `unreachable`、孤立 hub |
| 4 参考比对 | `tests/test_crosscheck.py` + `tests/reference.py` | 见下节 |
| 5 places | `tests/test_places.py` | 大小写 / 重音 / 空白 / NFD 输入；711 个城市原样及 NFD 形式都解析到自己；San Jose 三种情况；库里唯一的折叠冲突就是这一组；拼写错误候选；不存在城市；nearest_served 对**全部 711 个城市**与暴力参考（k=5）一致；Abu Dhabi → Tirana、Stuttgart → Novi Sad、Austin → Copenhagen；exclude / k |
| 7 线程一致性 | `tests/test_pricing.py` | 默认上下文的新线程、被改成 prec=6 / ROUND_UP 的线程、主线程 `localcontext(prec=4)`：报价字符串逐字节相同（每次都重新加载网络，无共享缓存） |
| 6 指纹 | `tests/test_network.py` | 临时副本：只改 mtime → 同一个 Network；改内容 → 重载、指纹变、报价变；mtime 和大小都不变的修改 → `strict=True` 能发现；改坏 → 持续报错，修好后恢复 |
| API | `tests/test_api.py` | `/health` 200 和计数；`/api/cities` 711 条 |

### 2.1 参考实现比对：覆盖范围和耗时

参考实现 `tests/reference.py` 不 import `app/` 的任何东西：自己读库、自己建图、自己做 Decimal 运算，搜索是
按起点做一次的 Bellman-Ford 松弛（只在 hub 之间松弛，非 hub 只作起点和终点），另有一个穷举所有合法简单路径的
`brute_force` 给小图用。两边只共享规格（见该文件开头的注释）。

- **真实库：全部有服务城市的有序对，701 × 700 = 490,700 对，全量，没有抽样。** 每对比较价格字符串、总距离字符串、
  城市序列，三者都必须相同；并逐对做结构检查：中间城市都是 hub、每段是真实航线、无重复城市、有直飞的 2,530 对
  结果都是直飞（要求 5）、各段之和与总距离相差 ≤ 航段数 × 1e-30、总距离 × 0.13 与价格相差 ≤ 1e-30。
  **耗时 27.6 s**（本机，`nice -n 10`；预算 120 s）。最长路线 8 段。
- **参考实现自检**：真实库的子网（前 9 个 hub + 它们的非 hub 城市，1000+ 对）上，Bellman-Ford 与穷举结果相同，
  生产实现也相同。
- **hypothesis 随机图**：400 例，2–8 个城市，坐标取自很小的网格（±1 / ±2 / ±3，另有 ±50），因此大量出现
  共线、等成本平手、重合点、不连通、无服务；每例对所有有序对比较「生产 vs 穷举」和「Bellman-Ford vs 穷举」，
  错误类型（`no_service` / `unreachable`）也要一致。另有 100 例随机破坏后的图必须被校验拒绝。
- **变异检查**（手工，未提交）：把端点枚举改成只看终点的第一个 hub，全量比对立即失败，说明比对有检出能力。

### 2.2 比对抓到的问题（已修）

随机图比对发现：`Kb(-1,-1) → Kc(2,2)` 有直飞（√18），也可经 `Ka(0,0)`（√2 + √8），两者价格相同，应选直飞，
生产实现却返回了两段的路线。原因是给 networkx 的整数边权用了 `d.scaleb(30)`——不带上下文参数的方法形式
走的是**线程默认的 28 位上下文**，边权被舍入，直飞候选被排除在容差之外。改为 `ctx.scaleb` /
`ctx.to_integral_exact`，并加了命名回归用例（含「调用方上下文是 prec=5 时候选表不变」）。真实库上没有受影响
（修复前后全量比对都通过），但这正是 M08 那一类错误，建议审查时把「Decimal 方法不带 context」当作检查点。

## 3. Gates

- `bash tool/test_diff.sh`：no new failures（0 known）
- `python3 tool/review_lint.py`：R01 ok，R02 ok
- `uv run uvicorn app.main:app --host 0.0.0.0 --port 8765`：日志 `Uvicorn running on http://0.0.0.0:8765`，
  `GET /health` → 200，`{"status":"ok","network":{"cities":711,"hubs":40,"hub_links":151,"unserved":10,…}}`

## 4. 偏离计划之处和原因

1. **hub 间候选路径不是「全对最短路 + `all_shortest_paths`」，而是 `nx.shortest_simple_paths`（Yen）取
   「整数最优 + 很小容差」内的全部简单路径。** 原因：规范成本是「未舍入各段之和再量化一次」，networkx 只能在
   它自己的边权上判断相等；无理数平手（√2 + √8 对 √18）在任何有限精度边权下都可能差最后一位，
   `all_shortest_paths` 会漏掉真正平手的路径，平手规则就不成立。做法：边权取 30 位量化后的整数（单位 1e-30 km，
   整数运算无误差），容差 `2 × (hub 数 + 2) + 16` 个单位——推导见 `build_hub_paths` 的注释；真实差异远大于此，
   所以容差只会多收候选，最终仍由 `price_path` 排序。真实库每个 hub 对恰好 1 条候选。
2. **排序键是量化后的价格**（要求写的是「最便宜」），其次航段数、城市名序列。平手 = 30 位价格字符串相同。
3. **「各段之和 = 总距离」在存储值上允许差 ≤ 航段数 × 1e-30。** 这是计划规定的算法（先求未舍入的和，再各自量化）
   的直接结果；没有采用「先把每段量化再求和」，那样会把舍入值再喂回计算，而且会让实数上相等的路线在 1e-30
   处分出先后。03 的「计算依据」展示如要逐位核对，请以总距离字段为准。
4. **坐标接受小数**（`"23.5,11"`），不只整数。真实库全是整数；规格只说「km 为单位的 2D 点」。带空格等其它
   格式仍然报错。
5. **「有服务」定义为「至少有一条直飞航线」。** 对非 hub 与要求 4 的定义相同；额外覆盖「没有任何连线的 hub」
   （真实库不存在，测试里有）。真实库无服务城市正好是 10 个 `connected_hubs` 为空的非 hub。
6. **`NetworkProvider.current(strict=True)`**：计划外的小补充。只看 mtime/size 的检查漏不掉的场景极少，但确实
   存在（同大小、mtime 被还原，测试里构造了）；03 的下单复核建议用 strict（61 KB 的 sha256，微秒级）。
7. **`RouteError.issues`**：两端同时有问题时一次返回两条（如两端都无服务），避免用户改一个再撞一个。
   `code` 取第一条。
8. **参考实现的 Bellman-Ford 是「每个起点跑一次」**（单源），不是字面上的每对跑一次；覆盖的城市对相同。
   它在每个 hub 上保留距最优 1e-20 以内的全部部分路径，理由同第 1 条。
9. 计划写 acceptance.md 由协调者记录比对范围和耗时；数字在本文件 2.1 节。

## 5. 给协调者的提示（不属于本迭代范围）

- starlette 1.10 的 TestClient 打印弃用警告：建议用 `httpx2` 代替 `httpx`。本迭代按计划仍用 httpx（测试通过）；
  是否换依赖请协调者决定。
- difflib 候选（cutoff 0.6）对完全不相干的输入也可能给出相似度不高的名字（如 `Atlantis` → Atlanta、Catania…）。
  它们只作为「候选」字段返回，从不自动采用；03 的文案 / 卡片要按「你是不是想找」呈现。
- `nearest_served` 不保证建议的城市与另一端连通（真实库是连通的，所以现在不会出问题）。
- hypothesis 用随机种子（失败样例存在 `backend/.hypothesis/`，已忽略）；每次运行覆盖略有不同，属有意为之。

## 6. Not proven at this tier

- 无（纯后端，tier A 足够）。真实库以外的大图性能未测：Yen 枚举在 40 个 hub 上约 0.1 s，hub 数量大一个数量级时
  加载会明显变慢，需要时再换成「全对 Dijkstra + 容差内剪枝枚举」。
