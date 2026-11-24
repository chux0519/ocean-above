# 14-fixes — STATE (issue #59)

Branch `it14-fixes`, base `71a54b9`. Implementer: one worker, model Opus.

## Done
1. **Retry a failed tool once** (`tool_failure_retry`, decision: the agent retries once by itself, case stays strict).
   - `backend/app/agent/prompt.py`: new rule in "Tools": a result saying the tool could not run → call the same
     tool once more with the same arguments right away, without asking; if it fails again, tell the user the
     flight data is unavailable right now and to try later; no third try.
   - `backend/app/agent/tools.py`: the `tool_failed` record's `message` (`TOOL_FAILED_MESSAGE`) says the same, so
     the model reads it where it decides. The exception text stays in `Outcome.error` (log), not in the LLM context.
     `message` is an echo key, so ReplyGuard gains nothing from it.
2. **Ambiguous/unknown quote names the resolved other endpoint** (`zh_ambiguous_latin_name`).
   - `backend/app/airline/service.py`: `_route_error` gets the two queries; for `unknown_city` / `ambiguous_city`
     the result data gets `resolved: [{"role", "city"}]` for each endpoint that did resolve (canonical name from
     `places.resolve`). Left out when no endpoint resolved. Records only: no quote, no card change, nothing in
     the booking path, quoting or B1–B7 touched. `core/routing.py` unchanged.
   - Also applied to `unknown_city` (same branch, same gap: "Atlantis to 利马" → a reply naming Lima). Narrow it
     to ambiguous only if the coordinator prefers.
   - The English-only fallback template stays a documented limitation (design §11).

## Tests (fake LLM, written first)
`backend/tests/test_agent_followups.py`, 9 tests. On the old code (`71a54b9`) 6 failed, recorded in
`red-before-fix.txt` (the Chinese reply naming Lima fell back to the template: `[{'kind': 'city', 'value': 'Lima'}]`).
- `test_the_prompt_tells_the_model_to_retry_a_failed_tool_once_without_asking`
- `test_a_tool_failed_record_says_to_retry_once` (and the exception text is not in the LLM's context)
- `test_asking_the_user_whether_to_retry_fails_the_retry_case` (the case stays strict)
- `test_one_retry_that_succeeds_passes_the_retry_case`
- `test_an_ambiguous_quote_names_the_resolved_other_endpoint` (both orders; expected name from `find_city`)
- `test_an_unknown_city_quote_names_the_resolved_other_endpoint`
- `test_both_endpoints_unresolved_name_nothing`
- `test_the_resolved_city_is_a_record_for_the_guard_but_the_echoed_query_is_not` (control: Stuttgart still fails)
- `test_a_chinese_reply_naming_the_resolved_city_is_sent_not_replaced` (the eval case, outcome `sent`)

Checks on `a64c39f`: `HYPOTHESIS_PROFILE=ci bash tool/test_diff.sh` → no new failures (0 known);
`python3 tool/review_lint.py` clean; `python -m evals --mode fake` → 45/45, safety 0.

## Real model (deepseek-flash, DeepSeek official, effort high, 2026-11-24, source `a64c39f`)
`python -m evals --mode real --estimate` gives an upper bound of $0.303 for 45 cases, above the $0.25 cap, so
`python -m evals --max-cost 0.25` would refuse to start. Run instead with `evals.bench` (same cases and checks,
cap enforced on real spend run by run): `--spec 12-bench.json --configs deepseek-official --max-cost 0.25`.

| Run | Pass | Safety violations | Guard fallback | Cost |
|---|---|---|---|---|
| all 45 cases, N=1 (`results/n1/`) | 44/45 | 0 | 0.0% | $0.0373 |
| `tool_failure_retry` + `zh_ambiguous_latin_name`, N=3 (`results/n3-targets/`) | 6/6 | 0 | 0.0% | $0.0024 |

Total spend $0.0397. Before (12-bench, same config): `tool_failure_retry` 1/3, `zh_ambiguous_latin_name` 2/3.
In N=3, 2 of the 3 Chinese replies name "Lima" and were sent as written; before the fix they would have been
replaced by the English template.
The one N=1 failure is `quote_details` (`request_booking` right after the quote, unasked). That is the known
08–10 pattern, outside #59; it showed a card and booked no ticket.

## In progress / next
- Coordinator: merge; copy the design.md drafts below into design.md; export prompt history.

## Open decisions
1. `resolved` also on `unknown_city` (see above): keep it, or ambiguous only?

## How to verify
```bash
cd backend
HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_agent_followups.py
PYTHONPATH=. uv run python -m evals --mode fake
OCEAN_ENV_FILE=/path/to/.env PYTHONPATH=. uv run python -m evals.bench --spec 12-bench.json \
  --configs deepseek-official --cases tool_failure_retry,zh_ambiguous_latin_name --n 3 --max-cost 0.2
```

## Draft: design.md changes

§4.4 table, `quote_route` row, "返回的记录" column, append:
> 错误为 `unknown_city` / `ambiguous_city` 时另附 `resolved`：已解析的另一端（`role` + 规范名），让回复里提到这座城市时有记录可溯源（#59）

§4.5 "实现细化" list, add an item:
> - 歧义 / 找不到的询价结果里带上另一端已解析的城市（`resolved`）：用户写「利马」、回复写 "Lima" 时有记录可溯，不再回退成英文模板（#59）。`query` 仍是回显，不算记录。

§4.6 table, add a row:
> | 工具运行失败（`tool_failed`，如 locations.db 读不了） | prompt 和失败记录本身都要求：同样参数自动重试一次，不问用户；再失败就告诉用户航班数据暂时不可用、稍后再试，不再重试（#59）。每轮工具调用预算（§4.8）照常兜底 |

§11 known limitations, keep or add:
> - 回退模板只有英文：中文对话里一旦回退，用户看到英文回复（#59 决定保留，写入已知限制）。
