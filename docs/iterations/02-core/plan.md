# 02-core — 后端脚手架 + 确定性计算核心

User request (2026-11-23): 01-architecture 计划确认（见 `../01-architecture/plan.md`「用户决定」）。

## Take-home check（2026-11-23）
- 要求 2/3/4/5/6/8 落在本迭代：只读 locations.db、hub 中转规则、最便宜路线、距离与价格、无服务城市 + 最近
  有服务城市。逐条见下方测试清单，无冲突。要求 7/9/10 属于 03/04，本迭代不涉及。

## Goal
`backend/` 可运行（FastAPI 健康检查），`core/` 给出经参考实现交叉验证的路线、距离、价格和城市解析。

## Scope
- 做：`backend/` uv 项目；`core/network.py`、`core/routing.py`、`core/pricing.py`、`core/places.py`；
  `GET /health`、`GET /api/cities`（调试用，可选）；测试和参考实现。
- 不做：app.db、LLM、agent、前端（03/04）。

## Design
- **network**：`load_network(path) -> Network`，`sqlite3` 以 `file:...?mode=ro` URI 只读打开。Network 不可变，
  带 `fingerprint`（mtime_ns + size + sha256）。`NetworkProvider.current()` 每次检查 mtime/size，变了再算
  sha256 并重载（03 迭代下单复核要用）。加载时校验：坐标格式、引用的 hub 存在且是 hub、hub 不带
  connected_hubs、hub_links 两端是 hub 且非自环；不合法直接报错，不静默修正。
- **pricing**：`leg_distance(a, b) -> Decimal`：`Decimal(dx*dx + dy*dy).sqrt()`，`localcontext(prec=50)`。
  `route_distance = sum(legs)`，`price = distance * Decimal("0.13")`；全部精确值。显示函数
  `fmt_km(d)`（1 位小数）、`fmt_usd(p)`（到分，ROUND_HALF_UP）只在输出层用。
- **routing**：`cheapest_route(net, origin, dest) -> Route | RouteError`。
  - 图 = hub 之间的 hub_links + 起点/终点各自到其 connected_hubs 的边（非 hub 城市只作端点，所以两个
    非 hub 城市之间不会经过第三个非 hub 城市）；边权 = 精确距离（Decimal）。
  - 用 `networkx.dijkstra_path`；平手处理：Dijkstra 后在所有等成本最短路中取航段最少、再按城市名序
    （`all_shortest_paths` 在等价时枚举；图很小，开销可忽略）。
  - 错误：`unknown_city`、`same_city`、`no_service(city, nearest_served)`、`unreachable`（数据现在连通，但代码必须处理）。
  - 返回：航段列表（from、to、精确距离）、中转城市、精确总距离、精确价格、所用 network 指纹。
- **places**：`resolve(name)`：NFKC + casefold + 去重音折叠后精确匹配 → 唯一则返回；否则 `difflib.get_close_matches`
  给 ≤ 5 个候选；不编造。`nearest_served(city)`：在有服务城市中按精确距离求最近（暴力 711 点；平手按名序）。
- **GitHub 先调研**（worker 开工第一步，结论写进 implementation.md）：搜「hub-constrained shortest path」
  「transfer only at hubs routing」等，确认没有比 networkx 更合适的成熟方案；如有再报协调者。

## Tests（pytest + hypothesis；`tool/run_tests.sh` 能跑）
1. 加载：真实库计数（711 / 40 / 151 / 10 无服务），坏数据样例库（坏坐标、坏引用、自环）各自报错。
2. 距离 / 价格：手算样例（3-4-5 三角形 → 5 km → $0.65）；精确值不被舍入（存储值 vs 显示值）；
   `fmt_usd` 在 .005 边界的舍入。
3. 路线规则：直飞 hub–hub、非 hub–其 hub、非 hub→非 hub 共享 hub、需多次中转；**任何路线的中间城市都是 hub**；
   每一段都是真实存在的航线；同城、未知城市、无服务（起点/终点各测一次，带最近城市）、构造的不可达图。
4. **参考实现交叉验证**：独立手写的参考（hub 间 Floyd-Warshall + 枚举起点 hub × 终点 hub），
   - 真实库：**全部有服务城市对**（约 700² ≈ 49 万对，参考解预计算后逐对比较成本；如超过 60 秒则改为
     固定种子随机抽 2 万对 + 全部 hub 对，并在验收中写明）；
   - hypothesis 随机生成的小图（含不连通、等成本平手、共线点）。
5. places：大小写、重音（bogota → Bogotá）、空白、拼写错误候选、不存在城市；nearest_served 与暴力参考一致。
6. 指纹：复制库到临时目录，修改后 `NetworkProvider` 重载且指纹改变。

## Work split
- 1 个 worker，**最强模型**（算法正确性），分支 `it02-core`，worktree `../ocean-above-it02`，拥有 `backend/**`、
  `tool/run_tests.sh`（如需调整）。依赖：fastapi、uvicorn、networkx；dev：pytest、hypothesis、httpx。
- 协调者同时写 03 的 API 契约草稿和 state_invariants（确认 / 下单 / 复核规则），不碰 `backend/`。
- worker 会话 id 记入 STATE.md，交付时导出到 `prompt history/`。

## Acceptance（tier A）
- `bash tool/test_diff.sh` 无新失败；`python3 tool/review_lint.py` 全过；`uv run uvicorn` 在 0.0.0.0 启动，`/health` 200。
- 交叉验证覆盖范围和耗时写进 acceptance.md；记录被测提交。
- 协调者审 diff：中间城市只能是 hub、精确值无提前舍入、错误类型齐全。

## Open questions for the user
- 无。确认后启动 worker。
