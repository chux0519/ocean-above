# LLM eval report — real mode

- Date: 2026-11-23 · commit `c5f461b`
- Model: `deepseek-flash` · reasoning effort: **high** · 10 run(s) per case · 2 cases · 20 runs · 40 chat turns
- Cost: **$0.0135** (80 LLM calls; tokens: 150,016 cached in / 15,821 uncached in / 6,542 out, of which 2,680 reasoning) — peak rates, deepseek-flash
- Latency: LLM call p50 1445 ms, p90 1882 ms, max 2240 ms; whole chat turn p50 2.92 s, p90 3.75 s
- Tokens per chat turn (charged against the 20,000 soft cap): p50 4,436, p90 4,803, max 4,912; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

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
| `quote_route` | 40 | 40 | 100.0% | ≥ 90.0% | 40 | 0 | 0 | PASS |
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
| no_service_then_nearest | no_service | 10/10 | 0/20 | 85,935 | 4,836 | $0.0067 |
| no_service_then_closest | no_service | 10/10 | 0/20 | 86,444 | 4,912 | $0.0068 |

## Failures (0 of 20 runs)

None.
