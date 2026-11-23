# STATE — 02-core round 01 (2026-11-23)

- **Done:** 分支 `it02-core`，已推送到 `origin/it02-core`。
  - `23de565` 后端脚手架：network / pricing / places / routing、`/health`、`/api/cities`
  - `3a6dcc3` 路线规则测试、参考实现全量比对、修复 hub 边权的默认上下文舍入 ← **被测提交**
  - 其后：文档提交（README「Backend development and tests」、本目录 implementation.md / STATE.md）
- **Tested source state:** `it02-core` @ `3a6dcc3`；105 passed；全量比对 490,700 对 27.6 s；
  `tool/test_diff.sh` 无新失败；`tool/review_lint.py` 全过；uvicorn 0.0.0.0 启动、`/health` 200。
- **In progress:** 无。
- **Next step:** 协调者审 diff（`git diff main...it02-core -- backend README.md`）并写 acceptance.md。
- **Open decisions (coordinator):**
  1. hub 候选路径用 Yen + 容差而不是 `all_shortest_paths`（implementation.md §4.1）——是否接受并同步 design.md §5。
  2. 存储值上「各段之和 = 总距离」允许差 ≤ 航段数 × 1e-30（§4.3）——03 的「计算依据」文案需据此措辞。
  3. 坐标接受小数、「有服务 = 至少一条直飞」、`current(strict=True)`、`RouteError.issues`（§4.4–4.7）。
  4. 是否把 dev 依赖 httpx 换成 httpx2（starlette 弃用警告，§5）。
- **How to verify:**
  - `cd backend && uv sync && uv run pytest -q`（约 40 s）；`uv run pytest -q -s -m slow` 打印比对范围和耗时
  - `bash tool/test_diff.sh && python3 tool/review_lint.py`
  - `cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`，另开终端 `curl localhost:8000/health`
- **Worker session:** Claude Code `6c19d2b6-f62b-4b7d-8048-ab879ac8ccc4`（worktree `ocean-above-it02`），交付时导出到 `prompt history/`。
