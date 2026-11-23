# STATE — 06-hardening P0a: per-turn agent budgets (issue #32)

- Branch `it06-budgets`; the tested source state is the PR head (budget commit `64ad917` merged with `origin/main`).
  Full backend suite `HYPOTHESIS_PROFILE=ci uv run pytest -q`: **729 passed**. `tool/test_diff.sh`: no new failures.
  `tool/review_lint.py`: ok.
- No new dependencies. Not touched: booking path, ReplyGuard rules, context.py, store, contract, evals.

## Done
| Item | Where | Test(s) (`backend/tests/test_agent_budget.py` unless named) |
|---|---|---|
| `AgentBudget` (frozen config, defaults 6 / 4 / 12 / 4096 / 20000 / 90 s) + `BudgetTracker` (per-turn tally) | `backend/app/agent/budget.py` | `test_defaults_match_the_issue_and_keep_the_old_limits`, `test_tracker_never_admits_more_than_the_budget` (hypothesis) |
| Env overrides `AGENT_MAX_STEPS`, `AGENT_MAX_TOOL_CALLS_PER_STEP`, `AGENT_MAX_TOOL_CALLS_PER_TURN`, `AGENT_MAX_OUTPUT_TOKENS_PER_CALL`, `AGENT_MAX_TOTAL_TOKENS_PER_TURN`, `AGENT_MAX_TURN_SECONDS`; integers (seconds may be a float), bounds checked, a bad value fails app startup | `budget.py` `from_env`, `api/settings.py` (`Settings.agent_budget`) | `test_budget_from_env`, `test_bad_budget_env_is_refused` (13 cases), `test_budget_values_are_positive_numbers` |
| Wiring: `Agent(budget=settings.agent_budget)`, `OpenAIChatClient(max_tokens=budget.max_output_tokens_per_call)` (2 lines in `api/routes.py install()`, agreed with the coordinator); `llm.MAX_TOKENS` now reads the budget default | `routes.py`, `llm.py` | `test_defaults_match…` |
| Test hooks: `Agent(budget=…)`; the old `max_steps=` / `turn_timeout=` keywords still work and override the budget's fields; `agent.max_steps` / `agent.turn_timeout` stay readable | `loop.py` | existing `test_agent_loop.py` unchanged and green |
| Tool calls counted one by one. Over the step or turn limit: **not executed** (no status event, no airline call), a structured `tool_budget_exceeded` result for every skipped call (so each `tool_call_id` has its tool message), stored as a `tool_calls` row with `error = "budget: per_step"/"per_turn"` (rebuilt history lines up with the logged completion). Per-step refusals: the turn goes on (the LLM may call again). A per-turn refusal: the turn ends with the template (`guard_problems = [{"kind":"tool_budget","value":"12"}]`) | `loop.py` `_steps` | `test_more_than_four_calls_in_one_completion_runs_only_four`, `test_crossing_twelve_calls_per_turn_ends_the_turn_without_running_more`, `test_turn_limit_reached_inside_a_step`, `test_exactly_using_the_turn_budget_still_lets_the_llm_answer` |
| Mutation check: with the gate patched to admit everything, the "not executed" check fails | — | `test_mutation_letting_refused_calls_through_is_caught` |
| A refusal is never a record the reply may quote (not added to the guard); the template ignores refusals (a refused `request_booking` gives no "check the confirmation card" sentence) | `loop.py`, `templates.py` | `test_refused_calls_do_not_reach_the_guard_records`, `test_refused_request_booking_changes_no_quote_state` (quote status unchanged, no confirm card, `book` never called, no ticket) |
| Turn token cap (soft) checked **before each LLM step** from `usage.total_tokens`; failed attempts and retries are charged too; missing usage → conservative estimate. Each `llm_calls.response` gets `budget: {charged_tokens, estimated, turn_tokens}`; the `tokens` column still holds only provider-reported usage (the site's daily budget, L2, is unchanged). Over the cap → template, `guard_problems = [{"kind":"token_budget","value":"20000"}]` | `budget.py` `charge`, `loop.py` `_llm_step` | `test_turn_token_cap_stops_before_the_next_step`, `test_missing_usage_is_charged_conservatively`, `test_failed_and_retried_calls_count`, `test_retries_can_exhaust_the_token_budget`, `test_token_helpers` |
| 90 s turn limit comes from the budget; behaviour unchanged | `loop.py` | `test_turn_seconds_come_from_the_budget`, existing `test_a_turn_has_a_total_time_limit`, `test_lock_and_turn_limits_have_the_round_02_defaults` |
| A budget stop logs `chat turn stopped by its budget … {steps, tool_calls, refused_calls, tokens, estimated_calls}` (warning) | `loop.py` | — |

## Open points / for the coordinator
1. **`.env.example` / compose**: the six `AGENT_*` variables are optional (defaults apply); add them to `.env.example`
   if you want them documented there (not in my file list).
2. **20 000 tokens per turn vs. long conversations**: the eval report shows ~3.5k tokens per single-turn case, but the
   history may reach 40k characters (~10k tokens) and every step re-sends it. A long conversation's 3-step turn
   (find_city → quote_route → text) can then cross the soft cap after step 2 and end with the template reply
   (still correct, built from the records). Worth watching in the P1 real-model run; raise
   `AGENT_MAX_TOTAL_TOKENS_PER_TURN` if it happens.
3. Missing-usage estimate makes a turn whose provider omits usage end after its first step when the cap is below
   ~4096 + input/2 (by design: conservative). DeepSeek sends usage with `stream_options.include_usage`.
4. `guard_problems` kinds gained `tool_budget` and `token_budget` (stored only; not in the contract's wire shapes).

## Next
- #33 (P0b concurrency / thread timeouts) builds on `AgentBudget` (same files).

## How to verify
```sh
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_agent_budget.py tests/test_agent_loop.py
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
```

## Draft for docs/design/design.md §4 (new §4.8 "每轮预算"; also add a row to §4.6 and two arrows to §4.1)

### 4.8 每轮预算（06 P0a，#32）
集中在 `agent/budget.py`：`AgentBudget`（配置）+ `BudgetTracker`（一轮的计数）。loop 只在两处问它：每次调 LLM 前
（`stop_before_step`）和每个工具调用前（`admit_tool_call`）。

| 预算 | 默认 | 环境变量（范围） | 超出时 |
|---|---|---|---|
| LLM 步数 | 6 | `AGENT_MAX_STEPS`（1–20） | 模板回复，`max_steps` |
| 每步工具调用 | 4 | `AGENT_MAX_TOOL_CALLS_PER_STEP`（1–16） | 多出的不执行，回结构化拒绝，本轮继续 |
| 每轮工具调用 | 12 | `AGENT_MAX_TOOL_CALLS_PER_TURN`（1–64） | 多出的不执行，回结构化拒绝，本步结束后模板回复，`tool_budget` |
| 单次输出 token（含思考） | 4096 | `AGENT_MAX_OUTPUT_TOKENS_PER_CALL`（256–32768） | 请求的 `max_tokens`；截断 → 模板（`truncated`） |
| 每轮总 token（软上限） | 20000 | `AGENT_MAX_TOTAL_TOKENS_PER_TURN`（1000–1000000） | 不再开始下一步，模板回复，`token_budget` |
| 每轮总时长 | 90 s | `AGENT_MAX_TURN_SECONDS`（5–600） | 模板回复（`turn_timeout`）；没有产出 → `llm_unavailable` |

环境变量不合法（非数字、越界、非整数）时应用启动失败，不会带着错误的预算运行。

- **工具调用逐个计数**，不是按 LLM 步计数。一次 completion 里超出的调用**不执行**（不发 `status`、不碰 airline），
  每个都得到一条 `{"code":"tool_budget_exceeded","message":…,"limit":"per_step"|"per_turn"}` 工具结果，
  所以每个 `tool_call_id` 都有回应，协议有效；拒绝也像普通调用一样存入 `tool_calls`（`error = "budget: …"`），
  下一轮重建的上下文与记录的 completion 对齐。拒绝不是记录：不进 ReplyGuard，模板也不引用它。
- **token 软上限**：用服务商回报的 `usage.total_tokens`，在两次 LLM 请求之间检查——调用前无法精确知道一次请求会用多少
  token，所以正在进行的那一步会跑完，超出后只是不再开始下一步。失败的调用和重试也计入；没有回报 usage 的调用（部分
  流式失败、某些服务商）按保守估计计：`ceil(请求 JSON 字符数 / 2) + 单次输出上限`。每次调用的计费写在
  `llm_calls.response.budget`（`charged_tokens`、`estimated`、`turn_tokens`）；`llm_calls.tokens` 仍只存服务商回报的值，
  全站每日预算（§8.2）照旧按它累计。**这不是严格的费用上限**：单次调用最多可以超出剩余额度一整步，价格也按模型而异。
- **预算停止永远不出票、不改 quote 状态**：agent 本来就没有出票路径；被拒的 `request_booking` 不执行，所以不会出现确认卡。
  已执行的工具结果和卡片照常保留，assistant 消息照常存储（模板），会话保持良构。
- 停止时记一条 warning 日志（步数、已执行/被拒调用数、token、估计次数）。

§4.6 加一行：
| 本轮工具调用或 token 超出预算 | 超出的工具调用不执行并回结构化拒绝；需要结束时模板回复（`tool_budget` / `token_budget`），见 §4.8 |

§4.1 状态机加：`ExecTools --> Fallback: 每轮工具调用用尽且有被拒调用`、`ExecTools --> Fallback: 本轮 token ≥ 软上限`。
