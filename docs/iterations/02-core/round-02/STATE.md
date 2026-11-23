# STATE — 02-core round 02 (2026-11-23)

- **Done:** 协调者审查后的两处小改动，分支 `it02-core` @ `52894ba`（被测提交），已推送。
  - a) 显示格式与契约一致：`fmt_km` → `8,928.6 km`，`fmt_usd` → `$1,160.71`（千分位；先在规范上下文里 HALF_UP 舍入，
    再插入分隔符）。新增测试：<1000、进位跨过 1,000（999.95 → `1,000.0 km`）、≥1,000,000、十亿级；
    并用真实库核对契约 fixture 里的值（Guangzhou → Lima 各段、总距离、价格，Abu Dhabi → Tirana 350.9 km）。
  - b) hypothesis 配置：`backend/tests/conftest.py` 注册 `ci`（`derandomize=True, database=None`）和 `dev`（默认，现状）；
    `tool/run_tests.sh` 以 `HYPOTHESIS_PROFILE=ci` 运行。连续两次 ci 运行结果一致（各 400 / 100 例）。
  - round 01 的 4 项决定：协调者全部接受（Yen + 容差；各段之和容差；小数坐标 / 有服务定义 / strict / issues；保留 httpx）。
- **Tested source state:** `it02-core` @ `52894ba`：117 passed（含全量比对 490,700 对，28.7 s）；`tool/test_diff.sh` 无新失败；
  `tool/review_lint.py` 全过。
- **In progress:** 无。
- **Next step:** 协调者审 PR 并合并到 main。
- **Open decisions:** 无。
- **How to verify:** `bash tool/test_diff.sh && python3 tool/review_lint.py`；
  `cd backend && uv run pytest -q`（dev 配置，随机样例）或 `HYPOTHESIS_PROFILE=ci uv run pytest -q`。
- **Worker session:** Claude Code `6c19d2b6-f62b-4b7d-8048-ab879ac8ccc4`。
