# STATE — 03-slice T2 store + airline (2026-11-23)

- **Done:** issue #1 全部范围，分支 `it03-airline`，已推送。
  - `backend/app/store/`：app.db schema（design §7 + `quote_cards`）、`BEGIN IMMEDIATE` 事务、可注入时钟、行级函数。
  - `backend/app/airline/`：`find_city`、`quote_route`、`request_booking`、`post_assistant_message`（done 时签发 nonce）、
    `book(user, quote, nonce)`（B1–B7）、`list_tickets`、`tickets`、`trace`、`messages`；契约卡片；会话锁。
  - 测试 128 个：单元（每个错误码）、契约 fixture 形状、并发、B1–B7 + Q1–Q3 固定种子模型测试、9 个变异体自检。
- **Tested source state:** `it03-airline` @ `6a19410`：全套 245 passed（约 52 s）；`tool/test_diff.sh` 无新失败；
  `tool/review_lint.py` 全过。之后只有文档提交。
- **In progress:** 无。
- **Next step:** 协调者审 PR（含只读评审）并合并；T3 / T5 按 `implementation.md` §2 的接口对接。
- **Open decisions（协调者）:**
  1. nonce 改为 HMAC 派生、不入库，`quote_cards` 表取代 §7 的 `quotes.nonce_hash` —— 接受的话 design §7 需同步。
  2. `nonce_secret` 默认每进程随机（重启后旧页面需刷新）；是否由 T5 / T7 加环境变量 `NONCE_SECRET`。
  3. `similar_names`（San Jose / San José 区分信息）目前只在 `Result.data`，契约 QuoteCard 没有字段；要不要进卡片。
  4. `trace.json` fixture 的最后一段 source 与真实库不符（Lima 是 hub），fixture 是示意值，未改。
- **How to verify:** `bash tool/test_diff.sh && python3 tool/review_lint.py`；
  `cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_booking_model.py tests/test_airline_booking.py`。
- **Notes:** 没有改 core、契约、`main.py`、`conftest.py`；无新增依赖；没有启动其他 agent。
