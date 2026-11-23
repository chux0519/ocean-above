# STATE — 03-slice T2 round 02 (2026-11-23)

- **Done:** 评审报告 8 条全部处理（明细和对应测试：`../implementation.md` §6），已合并 `origin/main`（新契约错误码、trace fixture 修正）。
  - #1 core：WAL 模式 / 非空 `-wal` 的 locations.db 直接拒绝（`NetworkDataError`），含命名回归用例。
  - #2 孤立代理字符 nonce → `invalid_nonce`。 #3 `book` 默认等锁 5 s → `conversation_busy`（409），另有 `book_async`。
  - #4 读不到库 → `data_unavailable`（503），不改 quote、不消耗 nonce；读到且不一致才 `stale`。
  - #5 `now` 在写事务内读取。 #6 `Store` 会话级方法加 `user_id` 并同事务查归属。 #7 两处小修。
  - #8 变异体 12 个（新增 `SkipsBookedCheck`、`TicketWithoutStatusChange`、`UnreadableMeansFresh`），核对规则号。
- **Tested source state:** `it03-airline` @ `ea798ca`（之后只有文档提交）：后端 263 passed（约 52 s）；
  `tool/test_diff.sh` 无新失败；`tool/review_lint.py` 全过。
- **In progress:** 无。
- **Next step:** 协调者复核并合并 PR #10。
- **Open decisions:** 无。
- **对其他任务的影响：** `Store.add_message / list_messages / add_llm_call / list_llm_calls / add_tool_call / list_tool_calls`
  的签名多了第一个参数 `user_id`（T3 若已按旧签名写，需要改）；`Airline.book` 可能抛的错误码多了
  `conversation_busy`、`data_unavailable`；`nonce_secret` 必须是 ≥ 16 字节的 `bytes`。
- **How to verify:** `bash tool/test_diff.sh && python3 tool/review_lint.py`；
  `cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q`。
- **Limits:** 本 worktree 未装前端依赖，`test_diff` 的前端部分这里没有实际跑到；改动不涉及前端。
