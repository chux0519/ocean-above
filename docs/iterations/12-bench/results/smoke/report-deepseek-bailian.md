# LLM eval report — real mode

- Date: 2026-11-24 · commit `0569a6f`
- Model: `deepseek-v4.1-flash-1017` · reasoning effort: **high** · 1 run(s) per case · 3 cases · 3 runs · 6 chat turns
- Cost: **unknown** (12 LLM calls, 0 failed / retried; tokens: 26,249 in, of which 13,568 cached / 773 out, of which 258 reasoning) — pricing 2026-11-24: owner's Bailian token plan: plan pricing may differ from the list price, which is not applied
- Latency: LLM call p50 1112 ms, p90 1248 ms, max 2191 ms; whole chat turn p50 2.33 s, p95 4.21 s; first streamed text p50 2.22 s, p95 3.99 s
- Tokens per chat turn (charged against the 60,000 soft cap): p50 4,631, p90 5,669, max 5,669; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

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
| book_flow | booking | 1/1 | 0/3 | 14,358 | 5,669 | unknown |
| no_service_then_nearest | no_service | 1/1 | 0/2 | 8,629 | 4,631 | unknown |
| zh_quote | chinese | 1/1 | 0/1 | 4,035 | 4,035 | unknown |

## Failures (0 of 3 runs)

None.

