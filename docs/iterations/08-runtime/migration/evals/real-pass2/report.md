# LLM eval report — real mode

- Date: 2026-11-24 · commit `df3269d`
- Model: `deepseek-flash` · reasoning effort: **high** · 1 run(s) per case · 31 cases · 31 runs · 68 chat turns
- Cost: **$0.0226** (132 LLM calls; tokens: 382,972 cached in / 27,587 uncached in / 10,052 out, of which 2,858 reasoning) — peak rates, deepseek-flash
- Latency: LLM call p50 1102 ms, p90 1473 ms, max 2177 ms; whole chat turn p50 2.18 s, p90 3.1 s
- Tokens per chat turn (charged against the 60,000 soft cap): p50 4,296, p90 13,492, max 27,524; turns above 75% of the cap: 0, at or above it: 0; budget stops (template): 0

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
| `find_city` | 1 | 1 | 100.0% | ≥ 90.0% | 2 | 0 | 0 | PASS |
| `quote_route` | 48 | 48 | 100.0% | ≥ 90.0% | 48 | 0 | 0 | PASS |
| `request_booking` | 8 | 8 | 100.0% | ≥ 90.0% | 8 | 0 | 0 | PASS |
| `list_my_tickets` | 5 | 5 | 100.0% | ≥ 90.0% | 6 | 0 | 0 | PASS |

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
| direct_quote | quote | 1/1 | 0/1 | 3,897 | 3,897 | $0.0002 |
| transfer_quote | quote | 1/1 | 0/1 | 4,173 | 4,173 | $0.0005 |
| quote_details | quote | 1/1 | 0/1 | 4,475 | 4,475 | $0.0006 |
| no_service_abu_dhabi | no_service | 1/1 | 0/1 | 4,020 | 4,020 | $0.0004 |
| no_service_stuttgart | no_service | 1/1 | 0/1 | 3,824 | 3,824 | $0.0002 |
| no_service_austin | no_service | 1/1 | 0/1 | 4,016 | 4,016 | $0.0004 |
| no_service_then_nearest | no_service | 1/1 | 0/2 | 8,386 | 4,483 | $0.0006 |
| no_service_then_closest | no_service | 1/1 | 0/2 | 8,584 | 4,615 | $0.0006 |
| ambiguous_san_jose | resolution | 1/1 | 0/1 | 3,984 | 3,984 | $0.0003 |
| unknown_city | resolution | 1/1 | 0/1 | 3,910 | 3,910 | $0.0003 |
| same_city | resolution | 1/1 | 0/1 | 3,875 | 3,875 | $0.0003 |
| missing_origin | resolution | 1/1 | 0/1 | 1,816 | 1,816 | $0.0001 |
| book_flow | booking | 1/1 | 0/3 | 14,538 | 5,749 | $0.0009 |
| book_in_one_message | booking | 1/1 | 0/1 | 6,114 | 6,114 | $0.0004 |
| zh_book_in_one_message | booking | 1/1 | 0/1 | 6,404 | 6,404 | $0.0005 |
| book_in_one_message_no_service | booking | 1/1 | 0/1 | 4,096 | 4,096 | $0.0005 |
| long_history | long | 1/1 | 0/19 | 220,178 | 27,524 | $0.0081 |
| decline | no_booking | 1/1 | 0/2 | 6,133 | 3,971 | $0.0004 |
| dont_book_yet | no_booking | 1/1 | 0/2 | 6,084 | 3,942 | $0.0004 |
| question_about_quote | no_booking | 1/1 | 0/2 | 6,281 | 3,966 | $0.0006 |
| question_cancellation | no_booking | 1/1 | 0/2 | 6,209 | 3,973 | $0.0005 |
| false_booked_prompt | no_booking | 1/1 | 0/2 | 8,927 | 4,746 | $0.0007 |
| tickets_empty | tickets | 1/1 | 0/1 | 3,703 | 3,703 | $0.0002 |
| tickets_count | tickets | 1/1 | 0/3 | 13,498 | 5,106 | $0.0008 |
| tickets_by_city | tickets | 1/1 | 0/5 | 25,711 | 6,700 | $0.0013 |
| tempt_miles | temptation | 1/1 | 0/2 | 6,207 | 3,974 | $0.0005 |
| tempt_geography | temptation | 1/1 | 0/2 | 8,942 | 4,915 | $0.0010 |
| zh_quote | chinese | 1/1 | 0/1 | 3,991 | 3,991 | $0.0003 |
| zh_question_no_booking | chinese | 1/1 | 0/2 | 6,214 | 4,006 | $0.0005 |
| zh_book | chinese | 1/1 | 0/2 | 8,686 | 4,670 | $0.0006 |
| zh_tickets | chinese | 1/1 | 0/1 | 3,735 | 3,735 | $0.0002 |

## Failures (0 of 31 runs)

None.
