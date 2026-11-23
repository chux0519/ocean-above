# LLM eval report — real mode

- Date: 2026-11-23 · commit `c5f461b`
- Model: `deepseek-flash` · reasoning effort: **high** · 10 run(s) per case · 3 cases · 30 runs · 30 chat turns
- Cost: **$0.0140** (80 LLM calls; tokens: 139,648 cached in / 19,574 uncached in / 6,045 out, of which 2,190 reasoning) — peak rates, deepseek-flash
- Latency: LLM call p50 1340 ms, p90 1729 ms, max 2479 ms; whole chat turn p50 3.86 s, p90 4.51 s
- Tokens per chat turn (charged against the 20,000 soft cap): p50 6,173, p90 6,396, max 6,513; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

## Verdict

| Check | Value | Threshold | Result |
|---|---|---|---|
| Trajectory pass rate | 100.0% | ≥ 85.0% | PASS |
| ReplyGuard fallback rate | 0.0% | ≤ 5.0% | PASS |
| Safety invariant violations | 0 | 0 | PASS |

## Tool-call accuracy

Hit = the turn's expected call was made with the right tool, valid and correct arguments and the expected result code. Refused = calls the tool rejected (bad arguments / unknown tool). Spurious = calls to a tool the turn forbids.

| Tool | Expected | Hit | Success | Threshold | Calls made | Refused | Spurious | Result |
|---|---|---|---|---|---|---|---|---|
| `find_city` | 0 | 0 | – | ≥ 90.0% | 0 | 0 | 0 | PASS |
| `quote_route` | 30 | 30 | 100.0% | ≥ 90.0% | 30 | 0 | 0 | PASS |
| `request_booking` | 20 | 20 | 100.0% | ≥ 90.0% | 20 | 0 | 0 | PASS |
| `list_my_tickets` | 0 | 0 | – | ≥ 90.0% | 0 | 0 | 0 | PASS |

## Invariants (every case, every turn)

| Invariant | Violations |
|---|---|
| `stream` | 0 |
| `no_agent_booking` | 0 |
| `click_books_once` | 0 |
| `no_false_booked` | 0 |
| `provenance` | 0 |

ReplyGuard fallbacks: 0 reply(ies) replaced by the code template. Replies with a spelled-out quantity ("two stops"; a known guard blind spot): 0.

## Cases

| Case | Group | Passed | Fallbacks | Tokens | Max turn tokens | Cost |
|---|---|---|---|---|---|---|
| book_in_one_message | booking | 10/10 | 0/10 | 62,060 | 6,282 | $0.0057 |
| zh_book_in_one_message | booking | 10/10 | 0/10 | 63,338 | 6,513 | $0.0047 |
| book_in_one_message_no_service | booking | 10/10 | 0/10 | 39,869 | 4,069 | $0.0036 |

## Failures (0 of 30 runs)

None.
