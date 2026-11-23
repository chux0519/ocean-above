# STATE — 03-slice T3 agent loop, tools, ReplyGuard, SSE events (2026-11-23)

- **Done:** issue #2 全部范围，分支 `it03-agent`，已推送。
  - `backend/app/agent/`：httpx OpenAI 兼容流式 client（thinking / reasoning effort：`LLM_REASONING_EFFORT` 默认 high + 按请求覆盖）、
    4 个工具 + 参数校验、ReplyGuard（数字 / 日期 / id / 库内城市按值溯源）、模板回退、agent loop（MAX_STEPS 6、重试退避、会话锁、
    断线照常落库、ping）、上下文重建（含 reasoning 回传）、SSE 编码。agent 无出票路径。
  - 真模型实测 5 次（31 场景 / 50 轮），最终 effort high：50/50 原文发出、0 回退；合计约 $0.07。
  - 测试新增 149 个（假 LLM 回放、mock transport、guard 属性测试）。
- **Tested source state:** `f53dfad`：394 passed；合并 main（T5 #13）后 `e72dc5d`：420 passed，`tool/test_diff.sh` 无新失败，lint 通过。
- **In progress:** 无。
- **Next step:** 协调者审 PR（含只读评审）；T5 按 implementation.md §2 接 `POST /api/conversations/{id}/messages`；T4 用 `FakeLLM` / `guard_probe.py` 起步。
- **Open decisions（协调者）:** 见 implementation.md §5，重点：
  1. 城市规则放宽到「用户在本会话任一消息提到过」（design §4.5 需同步）；
  2. `.env.example` 加 `LLM_REASONING_EFFORT`、`OCEAN_ENV_FILE`（T7 文件，未改）；
  3. `leg_count` / `transfer_count` 与 `city_names()` 建议挪进 airline；
  4. thinking 开启时 temperature 不生效，评测可重复性下降。
- **How to verify:** `bash tool/test_diff.sh && python3 tool/review_lint.py`；
  `cd backend && uv run pytest -q -W error tests/test_agent_guard.py tests/test_agent_llm.py tests/test_agent_loop.py`。
- **Notes:** 没有改 airline / store / core / 契约 / main.py；没有新依赖（httpx 移到运行依赖）；没有启动其他 agent；key 未打印、未入库、未提交。
