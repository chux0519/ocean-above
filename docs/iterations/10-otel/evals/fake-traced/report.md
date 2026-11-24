# LLM eval report — fake mode

- Date: 2026-11-24 · commit `4b73169` · tracing on (OTLP)
- Model: `scripted-fake` · reasoning effort: **n/a (fake)** · 1 run(s) per case · 31 cases · 31 runs · 68 chat turns
- Cost: **$0.0055** (130 LLM calls; tokens: 0 cached in / 13,000 uncached in / 1,300 out, of which 0 reasoning) — fake mode: not real usage
- Latency: LLM call p50 3 ms, p90 6 ms, max 18 ms; whole chat turn p50 0.02 s, p90 0.03 s
- Tokens per chat turn (charged against the 60,000 soft cap): p50 220, p90 220, max 330; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

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
| `find_city` | 1 | 1 | 100.0% | ≥ 90.0% | 1 | 0 | 0 | PASS |
| `quote_route` | 48 | 48 | 100.0% | ≥ 90.0% | 48 | 0 | 0 | PASS |
| `request_booking` | 8 | 8 | 100.0% | ≥ 90.0% | 8 | 0 | 0 | PASS |
| `list_my_tickets` | 5 | 5 | 100.0% | ≥ 90.0% | 5 | 0 | 0 | PASS |

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
| direct_quote | quote | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| transfer_quote | quote | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| quote_details | quote | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| no_service_abu_dhabi | no_service | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| no_service_stuttgart | no_service | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| no_service_austin | no_service | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| no_service_then_nearest | no_service | 1/1 | 0/2 | 440 | 220 | $0.0002 |
| no_service_then_closest | no_service | 1/1 | 0/2 | 440 | 220 | $0.0002 |
| ambiguous_san_jose | resolution | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| unknown_city | resolution | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| same_city | resolution | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| missing_origin | resolution | 1/1 | 0/1 | 110 | 110 | $0.0000 |
| book_flow | booking | 1/1 | 0/3 | 660 | 220 | $0.0003 |
| book_in_one_message | booking | 1/1 | 0/1 | 330 | 330 | $0.0001 |
| zh_book_in_one_message | booking | 1/1 | 0/1 | 330 | 330 | $0.0001 |
| book_in_one_message_no_service | booking | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| long_history | long | 1/1 | 0/19 | 4,290 | 330 | $0.0016 |
| decline | no_booking | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| dont_book_yet | no_booking | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| question_about_quote | no_booking | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| question_cancellation | no_booking | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| false_booked_prompt | no_booking | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| tickets_empty | tickets | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| tickets_count | tickets | 1/1 | 0/3 | 660 | 220 | $0.0003 |
| tickets_by_city | tickets | 1/1 | 0/5 | 1,100 | 220 | $0.0004 |
| tempt_miles | temptation | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| tempt_geography | temptation | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| zh_quote | chinese | 1/1 | 0/1 | 220 | 220 | $0.0001 |
| zh_question_no_booking | chinese | 1/1 | 0/2 | 330 | 220 | $0.0001 |
| zh_book | chinese | 1/1 | 0/2 | 440 | 220 | $0.0002 |
| zh_tickets | chinese | 1/1 | 0/1 | 220 | 220 | $0.0001 |

## Failures (0 of 31 runs)

None.
