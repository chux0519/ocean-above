# 02-core — 后端脚手架 + 确定性计算核心

User request (2026-11-23): 01-architecture 计划确认（见 `../01-architecture/plan.md`「用户决定」）。

## Take-home check（2026-11-23，设计评审后复查）
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
- **pricing**：**唯一的规范计价函数** `price_path(net, cities) -> Priced`：在固定上下文
  （`Context(prec=50, rounding=ROUND_HALF_EVEN)`，显式传入，不依赖线程默认上下文）里按航段顺序算
  `sqrt(dx²+dy²)`、求和、× `Decimal("0.13")`，每段距离、总距离、价格都量化为 30 位小数的字符串。报价、复核、测试
  都只用它。显示函数 `fmt_km`（1 位小数）、`fmt_usd`（到分，ROUND_HALF_UP）只在输出层用。措辞：「高精度值」，不叫「精确值」。
- **routing**：`cheapest_route(net, origin, dest) -> Route | RouteError`。
  - Network 加载时用 networkx 对 40 个 hub 求全对最短路径（边权用规范函数的单段距离）；查询时枚举
    「起点的直连 hub（hub 自身则只有自己）× 终点的直连 hub」，拼出候选城市序列，用 `price_path` 算成本取最小；
    平手依次按航段数、城市名序。非 hub 城市只作端点。**networkx 只用来选 hub 间路径，不使用它返回的长度。**
  - hub 间等成本的多条路径：用 `all_shortest_paths` 枚举后同样按规范成本 + 平手规则选定（图很小）。
  - 错误：`unknown_city`、`ambiguous_city`、`same_city`、`no_service(city, nearest_served 前 3，排除另一端)`、`unreachable`（数据现在连通，但代码必须处理）。
  - 返回：航段列表（from、to、精确距离）、中转城市、精确总距离、精确价格、所用 network 指纹。
- **places**：`resolve(name)`：先按原样（NFC、去首尾空白）精确匹配；不中再做 casefold + 去重音折叠匹配：
  命中唯一则返回，命中多个 → `ambiguous_city` + 全部候选（库里 `San Jose` / `San José` 就是这样一组），绝不自动选；
  仍不中 → `difflib.get_close_matches` 给 ≤ 5 个候选；不编造。命中的城市如有折叠同名兄弟，结果带 `similar_names`。
  加载时建立「折叠键 → 城市列表」映射（不能用一对一字典，否则后加载的会静默覆盖）。
  `nearest_served(city, k=3, exclude=())`：在有服务城市中按距离取前 k 个（暴力 711 点；平手按名序），可排除指定城市。
- **GitHub 先调研**（worker 开工第一步，结论写进 implementation.md）：搜「hub-constrained shortest path」
  「transfer only at hubs routing」等，确认没有比 networkx 更合适的成熟方案；如有再报协调者。

## Tests（pytest + hypothesis；`tool/run_tests.sh` 能跑）
1. 加载：真实库计数（711 / 40 / 151 / 10 无服务），坏数据样例库（坏坐标、坏引用、自环）各自报错。
2. 距离 / 价格：手算样例（3-4-5 三角形 → 5 km → $0.65）；精确值不被舍入（存储值 vs 显示值）；
   `fmt_usd` 在 .005 边界的舍入。
3. 路线规则：直飞 hub–hub、非 hub–其 hub、非 hub→非 hub 共享 hub、需多次中转；**任何路线的中间城市都是 hub**；
   每一段都是真实存在的航线；同城、未知城市、无服务（起点/终点各测一次，带最近城市）、构造的不可达图。
4. **参考实现交叉验证**：独立手写的参考（逐对 Bellman-Ford，只在 hub 换乘；自己构图、自己算距离，不共享生产代码），
   - 真实库：**全部有服务城市对**（约 49 万对；比较规范成本字符串和城市序列；如超过 120 秒则改为
     固定种子随机抽 2 万对 + 全部 hub 对，并在验收中写明）；
   - hypothesis 随机生成的小图（含不连通、等成本平手、共线点）。
5. places：大小写、重音（bogota → Bogotá）、空白、拼写错误候选、不存在城市；**`San Jose` 原样命中 San Jose、
   `San José` 原样命中 San José、`san jose` → ambiguous（两个候选）**；nearest_served 与暴力参考一致，
   Abu Dhabi → Tirana、Stuttgart → Novi Sad、Austin → Copenhagen；exclude 生效。
7. 线程一致性：在一个新线程（默认 Decimal 上下文）里算出的报价字符串与主线程逐字节相同。
6. 指纹：复制库到临时目录，修改后 `NetworkProvider` 重载且指纹改变。

## Work split
- 1 个 worker，**最强模型**（算法正确性），分支 `it02-core`，worktree `../ocean-above-it02`，拥有 `backend/**`、
  `tool/run_tests.sh`（如需调整）。依赖：fastapi、uvicorn、networkx；dev：pytest、hypothesis、httpx。
- worker 同时在 `README.md` 写「后端开发与测试」一节（增量交付）。
- 协调者同时写 03 的 API 契约草稿和 state_invariants（确认 / 下单 / 复核规则），不碰 `backend/`。
- worker 会话 id 记入 STATE.md，交付时导出到 `prompt history/`。

## Acceptance（tier A）
- `bash tool/test_diff.sh` 无新失败；`python3 tool/review_lint.py` 全过；`uv run uvicorn` 在 0.0.0.0 启动，`/health` 200。
- 交叉验证覆盖范围和耗时写进 acceptance.md；记录被测提交。
- 协调者审 diff：中间城市只能是 hub、精确值无提前舍入、错误类型齐全。

## Open questions for the user
- 无。确认后启动 worker。
