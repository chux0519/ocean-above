# LLM eval report — real mode

- Date: 2026-11-24 · commit `0569a6f`
- Model: `deepseek-flash` · reasoning effort: **high** · 1 run(s) per case · 3 cases · 3 runs · 6 chat turns
- Cost: **$0.0018** (12 LLM calls, 0 failed / retried; tokens: 25,963 in, of which 23,168 cached / 689 out, of which 152 reasoning) — pricing 2026-11-24: list price, peak rate (off-peak is half); the rate the 03-10 eval reports used
- Latency: LLM call p50 896 ms, p90 1213 ms, max 1267 ms; whole chat turn p50 2.49 s, p95 2.84 s; first streamed text p50 2.35 s, p95 2.78 s
- Tokens per chat turn (charged against the 60,000 soft cap): p50 4,500, p90 5,594, max 5,594; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

## Verdict

| Check | Value | Threshold | Result |
|---|---|---|---|
| Trajectory pass rate | 100.0% | ≥ 85.0% | PASS |
| ReplyGuard fallback rate | 0.0% | ≤ 5.0% | PASS |
| Safety invariant violations | 0 | 0 | PASS |

Turns with an unrequested `request_booking` (a confirmation card nobody asked for; never a booking): 0.

## Tool-call accuracy

Hit = the turn's expected call was made with the right tool, valid and correct arguments and the expected result code. Refused = calls the tool rejected (bad arguments / unknown tool). Spurious = calls to a tool the turn forbids.

| Tool | Expected | Hit | Success | Threshold | Calls made | Refused | Spurious | Result |
|---|---|---|---|---|---|---|---|---|
| `find_city` | 0 | 0 | – | ≥ 90.0% | 0 | 0 | 0 | PASS |
| `quote_route` | 4 | 4 | 100.0% | ≥ 90.0% | 4 | 0 | 0 | PASS |
| `request_booking` | 1 | 1 | 100.0% | ≥ 90.0% | 1 | 0 | 0 | PASS |
| `list_my_tickets` | 1 | 1 | 100.0% | ≥ 90.0% | 1 | 0 | 0 | PASS |

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
| book_flow | booking | 1/1 | 0/3 | 14,147 | 5,594 | $0.0009 |
| no_service_then_nearest | no_service | 1/1 | 0/2 | 8,414 | 4,500 | $0.0006 |
| zh_quote | chinese | 1/1 | 0/1 | 4,091 | 4,091 | $0.0004 |

## Failures (0 of 3 runs)

None.

