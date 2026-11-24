# LLM eval report — real mode

- Date: 2026-11-24 · commit `a64c39f`
- Model: `deepseek-flash` · reasoning effort: **high** · 3 run(s) per case · 2 cases · 6 runs · 6 chat turns
- Cost: **$0.0024** (15 LLM calls, 0 failed / retried; tokens: 29,960 in, of which 26,880 cached / 1,091 out, of which 323 reasoning) — pricing 2026-11-24: list price, peak rate (off-peak is half); the rate the 03-10 eval reports used
- Latency: LLM call p50 1095 ms, p90 1459 ms, max 1845 ms; whole chat turn p50 3.09 s, p95 3.54 s; first streamed text p50 2.93 s, p95 3.52 s
- Tokens per chat turn (charged against the 60,000 soft cap): p50 6,142, p90 6,202, max 6,202; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

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
| `quote_route` | 6 | 6 | 100.0% | ≥ 90.0% | 9 | 3 | 0 | PASS |
| `request_booking` | 0 | 0 | – | ≥ 90.0% | 0 | 0 | 0 | PASS |
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
| tool_failure_retry | robustness | 3/3 | 0/3 | 18,525 | 6,202 | $0.0012 |
| zh_ambiguous_latin_name | chinese | 3/3 | 0/3 | 12,526 | 4,197 | $0.0012 |

## Failures (0 of 6 runs)

None.

