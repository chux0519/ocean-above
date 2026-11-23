# LLM eval report — real mode

- Date: 2026-11-23 · commit `d61cff8`
- Model: `deepseek-flash` · reasoning effort: **high** · 10 run(s) per case · 1 cases · 10 runs · 20 chat turns
- Cost: **$0.0086** (40 LLM calls; tokens: 66,816 cached in / 8,925 uncached in / 4,636 out, of which 2,742 reasoning) — peak rates, deepseek-flash
- Latency: LLM call p50 1131 ms, p90 2323 ms, max 5018 ms; whole chat turn p50 2.61 s, p90 4.39 s

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
| `quote_route` | 20 | 20 | 100.0% | ≥ 90.0% | 20 | 0 | 0 | PASS |
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

| Case | Group | Passed | Fallbacks | Tokens | Cost |
|---|---|---|---|---|---|
| no_service_then_nearest | no_service | 10/10 | 0/20 | 80,377 | $0.0086 |

## Failures (0 of 10 runs)

None.
