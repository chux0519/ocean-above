# STATE — 06-hardening P1: eval regressions (issue #34)

- Branch `it06-evals` (from `main` d61cff8, after P0a #32). Tested source state: PR head. Full backend suite
  `HYPOTHESIS_PROFILE=ci uv run pytest -q`: **773 passed** (after merging origin/main 9499e76); `tool/test_diff.sh`: no new failures; `tool/review_lint.py`: ok.
- No new dependencies. Not touched: booking path (`Airline.book`, nonce, idempotency), ReplyGuard rules (`guard.py`),
  `loop.py` (P0b #33 works there), context.py, airline/core/store. B1–B7 and the reference routing tests are unchanged and green.

## Root causes (from the T4 transcripts and the baseline below)
1. **no_service_then_nearest**: the prompt said "Never pick for them" after a no-service result, so on "ok, use the
   nearest city instead" the model sometimes refused ("I can't pick the nearest for you"). It also had to work out
   which listed city was nearest by itself (the list is ordered, but nothing said so).
2. **book_in_one_message**: the prompt only tied `request_booking` to a *later* "yes / book it". On "Book me a flight
   from Madrid to Cairo" the model quoted and then asked "Shall I prepare the booking?" (5/10 in the baseline). In 2 of
   those 5 it even told the user to click "Confirm booking" when no confirmation card existed.

## Done
| Item | Where | Test(s) |
|---|---|---|
| `nearest_served_city`: code names the single nearest served city on every no_service record (`find_city` result, each `quote_route` issue) = `nearest[0]`, the airline's distance order. Left out when the first two are equally near at display precision (then the user chooses). The LLM compares nothing | `agent/tools.py` `_mark_nearest` | `tests/test_eval_regressions.py`: `test_nearest_served_city_in_the_quote_result_is_the_reference_nearest` (Austin, Abu Dhabi, both cities unserved), `test_find_city_names_the_nearest_served_city_too`, `test_every_unserved_city_gets_the_reference_nearest_or_none_on_a_tie` (all unserved cities in locations.db vs the brute-force `RefGraph`), `test_no_nearest_hint_when_the_first_two_are_equally_near` |
| Prompt: "use the nearest one" is the user's choice → `quote_route` with `nearest_served_city` in place of the unserved city, other city unchanged, no second question; no hint → ask. "Book me a flight from A to B" → `quote_route`, then (code ok, no `similar_names`) `request_booking` in the same turn; any other result → ask. "Only the click issues the ticket" unchanged | `agent/prompt.py` | `test_the_prompt_keeps_both_rules` |
| Named fake-LLM regressions: good trajectory passes; the observed bad ones fail the checks | `tests/test_eval_regressions.py` | `test_regression_no_service_then_nearest_quotes_the_nearest_city`, `test_regression_closest_destination_is_replaced_and_the_origin_kept`, `test_the_observed_refusal_to_pick_the_nearest_is_a_failure`, `test_a_different_city_than_the_nearest_is_a_failure`, `test_regression_book_in_one_message_shows_the_confirm_card_and_books_only_on_click`, `test_regression_book_in_one_message_in_chinese`, `test_the_observed_quote_without_confirm_card_is_a_failure`, `test_booking_asked_for_an_unserved_city_gets_no_confirm_card` |
| Eval cases tightened: `no_service_then_nearest` (forbids `request_booking`; the fake reads `nearest_served_city` from the rebuilt history), `book_in_one_message` (+ a `Click`: no ticket during the turn, exactly one after the click). New: `no_service_then_closest` (destination replaced, other wording), `zh_book_in_one_message`, `book_in_one_message_no_service` (asked to book, no route → no confirm card), `long_history` (18 quotes + book-in-one-message + click) | `evals/cases.py`, `evals/fake.py` | `tests/test_evals.py` (every case passes with the scripted model) |
| Eval report: tokens each chat turn charged against the per-turn soft cap (p50/p90/max, turns near/over the cap, budget stops; per-case max) | `evals/runner.py`, `evals/checks.py`, `evals/report.py` | fake run |
| **Turn token soft cap default 20 000 → 60 000** (P0a open point 2; data below) | `agent/budget.py`, `.env.example`, `test_defaults_match…` | `tests/test_agent_budget.py` |

## Real-model evals (deepseek-flash, effort high; reports and raw transcripts in `evals/`)
| Run | Code | Runs | Trajectory pass | Fallbacks | Invariant violations | Cost |
|---|---|---|---|---|---|---|
| `before/` full suite, N=3 | d61cff8 (27 old cases; the "+uncommitted" label was taken at the end of the run, the code was imported at start, before any edit) | 81 | 97.5% (book_in_one_message 2/3, tempt_geography 2/3) | 0 | 0 | $0.0403 |
| `before10-book_in_one_message/` N=10 | d61cff8 | 10 | **50%** | 0 | 0 | $0.0039 |
| `before10-no_service_then_nearest/` N=10 | d61cff8 | 10 | 100% (T4 had 2/3) | 0 | 0 | $0.0086 |
| `after/` full suite, N=3 | c5f461b (31 cases) | 93 | **100%** | 0 | 0 | $0.0671 |
| `after10-book_in_one_message/` (3 booking cases) N=10 | c5f461b | 30 | **100%** | 0 | 0 | $0.0140 |
| `after10-no_service_then/` (nearest + closest) N=10 | c5f461b | 20 | **100%** | 0 | 0 | $0.0135 |

Total real-model cost of this task: **$0.147**. The cap change (60k) came after these runs; no run had a budget stop, so
it does not change any of them.

Remaining stochastic failures: none in the after runs (143 runs). Seen in the baseline only: `tempt_geography` 1/3 (the
model offered Dubai, which is served, instead of quoting Tirana from the card — not addressed here, it was not in the
T4 failures), and "Replies with a spelled-out quantity" (guard blind spot, 2 in the after run; reported, not a failure).
The `no_service_then_nearest` refusal is rare (1/3 in T4, 0/13 since); the after numbers show the prompt + hint hold,
not that the old rate was high.

## P0a open point 2: does the 20k per-turn soft cap trip in long conversations?
Measured in `after/` (`long_history`, 19 chat turns, 3 runs); per-turn tokens = provider `total_tokens` of all calls:
- ordinary single-turn cases: max 3.8k–4.7k per turn; book_in_one_message (3 steps): 6.3k; tickets_by_city: 7.1k.
- `long_history` turns 14–18 (2 steps each): 13.8k → 17.3k, ~+0.8k per earlier turn; turn 19 (3 steps:
  quote, request_booking, text): **26.4k / 26.7k / 27.3k**, i.e. ~18k after step 2 — just under the cap, so step 3 still ran.
- So with 20k, a 3-step turn (book-in-one-message, find_city → quote) late in a conversation is 2–3 turns away from
  being cut before its last step and ending with the template reply (safe but worse), and once the history reaches
  its 40k-character budget (~13k tokens) every 3-step turn would end that way.
- **Changed the default to 60 000**: per step ≈ system prompt + tools (~2.5k) + history at budget (~13k) ≈ 16k, so four
  steps fit; a runaway turn is still stopped by `max_steps=6` and the 12-tool-call limit. Cost impact is negligible on
  deepseek-flash (mostly cached input: a whole 19-turn `long_history` run cost ≈ $0.008). `AGENT_MAX_TOTAL_TOKENS_PER_TURN` still overrides.

## Open points / for the coordinator
1. design.md §4.8 table: change the default "每轮总 token（软上限）" 20000 → 60000 and add the reason (one line, data above).
   §4 prompt description, if it lists prompt rules: add "nearest_served_city" and "book in one message" (draft below).
2. `.env.example` (outside the issue's file list) was edited only to keep its listed default in step with `budget.py`
   (otherwise a copied `.env` would silently keep 20000).
3. `review-runtime.md` (merged into main during this task) P2-6 predicted exactly this ("20 000 对长会话偏紧", not run
   against a real model); the `long_history` numbers above confirm it. 60k also softens its P1-2 (an estimated failed
   attempt ≈ chars/2 + 4096 no longer ends a mid-length turn on its own), but P1-2's fix itself (do not charge
   failed attempts that consumed nothing) belongs to loop/budget work, not done here.
4. `nearest_served_city` is an LLM-facing field only (tool result JSON); the card and the contract are unchanged.

### Draft for design.md
- §4 tools: no_service records carry `nearest_served_city` (code: `nearest[0]`, left out on a display-precision tie);
  "use the nearest city" is a user choice and is quoted directly; without the field the agent asks.
- §4 booking: "Book me a flight from A to B" = quote + confirmation card in the same turn; the ticket still only comes
  from the user's click (the card's nonce), so explicit confirmation of the full route, distance and price is kept.
- §4.8: default 60000 (was 20000; P1 measurement: a 3-step turn in a 19-turn conversation reached ~27k).

## How to verify
```sh
cd backend && HYPOTHESIS_PROFILE=ci uv run pytest -q tests/test_eval_regressions.py tests/test_evals.py tests/test_agent_budget.py
uv run python -m evals --mode fake --n 2
# real (costs ~$0.07, needs the key via OCEAN_ENV_FILE): uv run python -m evals --mode real --n 3 --effort high --out /tmp/x
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
```
